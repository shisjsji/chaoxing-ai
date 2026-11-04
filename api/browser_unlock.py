"""内嵌浏览器的两件底层能力：解除粘贴限制、把登录态搬进来。

两个都踩过坑，记在这里：

1. `CoreWebView2` **只能在 WinForms 的 UI 线程访问**，在后台线程直接读属性会抛
   `CoreWebView2 can only be accessed from the UI thread`。所以一律走
   `form.Invoke(...)` 切回去。

2. 程序的登录是 requests + cookies.txt，内嵌浏览器是 WebView2 自己的 cookie 库，
   两套完全不互通。用户在设置里登录过之后，浏览器里还得再登一次。这里把
   cookies.txt 里的条目塞进 WebView2 的 CookieManager，导航过去就是登录状态。
"""

import time

from api.logger import logger

FREE_EVENTS = ("paste", "copy", "cut", "contextmenu", "selectstart", "dragstart")

UNLOCK_JS = """
(function () {
  if (window.__cxPasteFree) return;
  window.__cxPasteFree = true;

  var FREE = %s;
  var rawPrevent = Event.prototype.preventDefault;
  Event.prototype.preventDefault = function () {
    if (FREE[this.type]) return;
    return rawPrevent.apply(this, arguments);
  };

  function loosen(el) {
    if (!el || !el.style) return;
    el.onpaste = null;
    el.oncopy = null;
    el.oncut = null;
    el.oncontextmenu = null;
    el.style.userSelect = 'text';
    el.style.webkitUserSelect = 'text';
  }

  function sweep() {
    loosen(document.body);
    var list = document.querySelectorAll('[contenteditable], textarea, input');
    for (var i = 0; i < list.length; i++) loosen(list[i]);
  }

  sweep();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', sweep);
  }
  document.addEventListener('focusin', function (e) { loosen(e.target); }, true);
  window.addEventListener('load', sweep);
})();
""" % (
    "{"
    + ", ".join("%s: 1" % e for e in FREE_EVENTS)
    + "}"
)

_installed = set()
_synced = set()


def _form_of(win):
    return getattr(win, "native", None)


def _on_ui(form, fn, default=None):
    """把 fn 丢回 WinForms 的 UI 线程执行，并等它跑完。"""
    if form is None:
        return default
    try:
        from System import Action
    except Exception:
        return default

    box = {"ok": False, "value": default}

    def run():
        try:
            box["value"] = fn()
            box["ok"] = True
        except Exception as exc:
            logger.debug(f"UI 线程执行失败：{exc}")

    try:
        form.Invoke(Action(run))
    except Exception as exc:
        logger.debug(f"切 UI 线程失败：{exc}")
        return default
    return box["value"] if box["ok"] else default


def _core_of(form):
    def grab():
        for attr in ("webview", "web_view"):
            ctrl = getattr(form, attr, None)
            if ctrl is None:
                continue
            core = getattr(ctrl, "CoreWebView2", None)
            if core is not None:
                return core
        browser = getattr(form, "browser", None)
        ctrl = getattr(browser, "webview", None) if browser is not None else None
        if ctrl is not None:
            return getattr(ctrl, "CoreWebView2", None)
        return None

    return _on_ui(form, grab)


def install(win, label: str = "browser") -> bool:
    """给窗口装上解锁脚本。失败返回 False，不影响主流程。"""
    if win is None:
        return False
    if id(win) in _installed:
        return True

    form = _form_of(win)
    core = _core_of(form)
    if core is None:
        return False

    JS = UNLOCK_JS

    def add():
        core.AddScriptToExecuteOnDocumentCreatedAsync(JS)
        return True

    if not _on_ui(form, add):
        return False

    _installed.add(id(win))
    logger.info(f"{label} 已解除复制粘贴限制")
    return True


def install_later(win, label: str = "browser", tries: int = 60, gap: float = 0.4) -> bool:
    for _ in range(max(1, tries)):
        if install(win, label):
            return True
        time.sleep(gap)
    logger.debug(f"{label} 始终没拿到 CoreWebView2，跳过解锁")
    return False


# ---------- 登录态同步 ----------

# 学习通的 cookie 都挂在这个域下，子域共享，写一条就够了
COOKIE_DOMAINS = (".chaoxing.com", "chaoxing.com")


def _load_cookies(path: str = "cookies.txt") -> list:
    """读 requests 存下来的 cookies。

    两种格式都要认：
      - 一整行 `JSESSIONID=x; UID=y; ...`（本项目现在存的就是这种）
      - Netscape 逐行制表符格式（老版本和 curl 导出常见）
    """
    import os

    if not os.path.exists(path):
        return []
    try:
        text = open(path, "r", encoding="utf-8", errors="replace").read()
    except OSError as exc:
        logger.debug(f"读 cookies 失败：{exc}")
        return []

    out = []
    lines = [ln for ln in text.split("\n") if ln.strip() and not ln.startswith("#")]

    # Netscape：7 个字段用 tab 隔开
    if any("\t" in ln for ln in lines):
        for line in lines:
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _flag, cpath, _secure, _exp, name, value = parts[:7]
            out.append({
                "domain": domain.lstrip("."),
                "path": cpath or "/",
                "name": name.strip(),
                "value": value.strip(),
            })
        return out

    # Cookie 头：k=v; k=v
    for chunk in text.replace("\n", ";").split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        name, _, value = chunk.partition("=")
        name = name.strip()
        if not name:
            continue
        out.append({"name": name, "value": value.strip(),
                    "domain": "", "path": "/"})
    return out


def sync_cookies(win, label: str = "browser", force: bool = False) -> int:
    """把 cookies 写进 WebView2，返回写入条数。

    force=True 时忽略「已经同步过」的记录。用户可能在设置里重新登录过，
    打开页面之前得把最新的 cookie 再推进去一次。
    """
    if win is None:
        return 0
    if not force and id(win) in _synced:
        return -1

    cookies = _load_cookies()
    if not cookies:
        return 0

    form = _form_of(win)
    core = _core_of(form)
    if core is None:
        return 0

    domains = COOKIE_DOMAINS
    items = cookies

    def push():
        manager = core.CookieManager
        if manager is None:
            return 0
        n = 0
        for item in items:
            targets = [item["domain"]] if item["domain"] else list(domains)
            for domain in targets:
                try:
                    cookie = manager.CreateCookie(item["name"], item["value"],
                                                  domain, item["path"] or "/")
                    manager.AddOrUpdateCookie(cookie)
                    n += 1
                except Exception:
                    continue
        return n

    count = _on_ui(form, push) or 0
    if count:
        _synced.add(id(win))
        logger.info(f"{label} 已同步 {count} 条登录 cookie")
    return count


def sync_cookies_later(win, label: str = "browser", tries: int = 60, gap: float = 0.4) -> int:
    for _ in range(max(1, tries)):
        n = sync_cookies(win, label)
        if n:
            return n
        time.sleep(gap)
    return 0


def prepare(win, label: str) -> None:
    """把两个能力一起装上。

    cookie 这里只等几秒：窗口创建时用户可能还没登录，
    真正的同步放在每次打开页面之前做。
    """
    install_later(win, label)
    sync_cookies_later(win, label, tries=12)