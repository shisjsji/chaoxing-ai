"""卸载程序：让用户自己决定本地资料留不留。

界面用 pywebview 打开，业务走本地 HTTP 接口，和安装器同一套做法。
文件删除交给一个独立的 PowerShell 脚本 —— 卸载器自己也在要删的目录里，
不能一边运行一边删自己；脚本用 UTF-8 BOM 保存，中文安装路径不会变形。

用法：
  无参数              图形卸载（可勾选是否删除资料）
  --silent            静默卸载，保留本地资料
  --silent --delete-data  静默卸载并删除资料
  --dir 路径          指定安装目录（内部测试与调试用）
"""

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request

from install_common import (
    APP_DISPLAY,
    DATA_ITEMS,
    DEFAULT_DIR,
    EXE_NAME,
    UNINSTALLER_NAME,
    delete_registry,
    dir_size,
    human_size,
    read_installed,
    remove_shortcuts,
    taskkill_app,
    webview_profile_dir,
)

app = Flask("uninstaller", static_folder=None)

STATE = {
    "window": None,
    "running": False,
    "error": "",
    "log": "",
}


def install_dir() -> Path:
    for idx, arg in enumerate(sys.argv):
        if arg == "--dir" and idx + 1 < len(sys.argv):
            return Path(sys.argv[idx + 1])
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return DEFAULT_DIR


def looks_like_install(target: Path) -> bool:
    return (target / EXE_NAME).exists()


def collect_info() -> dict:
    target = install_dir()
    program = sum(dir_size(target / name) for name in
                  ("_internal", EXE_NAME, UNINSTALLER_NAME, "uninstall.exe"))
    data = []
    for name, label in DATA_ITEMS:
        path = target / name
        if path.exists():
            data.append({"name": name, "label": label,
                         "size": human_size(dir_size(path)), "is_dir": False})
    profile = webview_profile_dir()
    if profile.exists():
        data.append({"name": "webview", "label": "考场窗口登录缓存",
                     "size": human_size(dir_size(profile)), "is_dir": True})
    installed = read_installed()
    return {
        "path": str(target),
        "valid": looks_like_install(target),
        "version": installed.get("version", ""),
        "program_size": human_size(program),
        "data_size": human_size(sum(dir_size(target / n) for n, _ in DATA_ITEMS)
                                + dir_size(profile)),
        "items": data,
    }


def build_script(target: Path, keep_data: bool, owner_pid: int) -> str:
    """等卸载器自己退出后再删文件。"""
    log = Path(tempfile.gettempdir()) / "chaoxing_uninstall.log"
    app_q = str(target).replace("'", "''")
    profile_q = str(webview_profile_dir()).replace("'", "''")
    log_q = str(log).replace("'", "''")

    if keep_data:
        action = f"""
    Remove-Item -LiteralPath (Join-Path $app '_internal') -Recurse -Force -ErrorAction SilentlyContinue
    foreach ($f in @('{EXE_NAME}', '{UNINSTALLER_NAME}', 'uninstall.exe')) {{
        Remove-Item -LiteralPath (Join-Path $app $f) -Force -ErrorAction SilentlyContinue
    }}
    Note '程序文件已删除，本地资料保留'"""
    else:
        action = f"""
    Remove-Item -LiteralPath $app -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath '{profile_q}' -Recurse -Force -ErrorAction SilentlyContinue
    Note '程序与本地资料已全部删除'"""

    return f"""$log = '{log_q}'
$app = '{app_q}'
$owner = {owner_pid}
function Note($m) {{ try {{ "$(Get-Date -Format 'HH:mm:ss')  $m" | Out-File -LiteralPath $log -Append -Encoding utf8 }} catch {{}} }}

Note '=== 卸载开始 ==='

# 等卸载器自己退出：它还占着自己的 exe 和 _internal 里的文件
for ($i = 0; $i -lt 40; $i++) {{
    if (-not (Get-Process -Id $owner -ErrorAction SilentlyContinue)) {{ break }}
    Start-Sleep -Milliseconds 300
}}
Start-Sleep -Milliseconds 600
{action}
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
"""


def spawn_script(script: str) -> None:
    path = Path(tempfile.gettempdir()) / "chaoxing_uninstall.ps1"
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as fp:
        fp.write(script)
    # 不能换 DETACHED_PROCESS：没有控制台的进程里拉起的 powershell 会静默退出
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-WindowStyle", "Hidden", "-File", str(path)],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        close_fds=True,
    )


def do_uninstall(keep_data: bool) -> None:
    """执行清理。调用前必须已经确认这是安装目录。"""
    target = install_dir()
    script = build_script(target, keep_data, os.getpid())
    taskkill_app()
    remove_shortcuts()
    delete_registry()
    try:
        spawn_script(script)
    except Exception as exc:
        STATE.update(running=False, error=f"无法启动清理程序：{exc}")
        return
    STATE.update(log="程序文件正在删除" if keep_data else "程序与本地资料正在删除")


@app.get("/")
def index():
    return HTML


@app.get("/api/info")
def api_info():
    return jsonify(collect_info())


@app.post("/api/uninstall")
def api_uninstall():
    if not looks_like_install(install_dir()):
        return jsonify(ok=False, error="这个目录里没有主程序，未做任何删除"), 200
    data = request.get_json(silent=True) or {}
    keep_data = bool(data.get("keep_data", True))
    STATE.update(running=True, error="", log="正在关闭程序并清理文件…")
    threading.Thread(target=do_uninstall, args=(keep_data,), daemon=True).start()
    return jsonify(ok=True, error="", log=STATE["log"])


@app.get("/api/state")
def api_state():
    return jsonify(running=STATE["running"], error=STATE["error"], log=STATE["log"])


@app.post("/api/close")
def api_close():
    window = STATE.get("window")
    if window is not None:
        threading.Timer(0.3, lambda: window.destroy()).start()
    return jsonify(ok=True)


def silent_uninstall(keep_data: bool) -> int:
    target = install_dir()
    if not looks_like_install(target):
        print(f"这里不像安装目录：{target}")
        return 1
    do_uninstall(keep_data)
    if STATE["error"]:
        print(STATE["error"])
        return 1
    time.sleep(1.0)
    print("已安排删除：" + ("保留本地资料" if keep_data else "同时删除本地资料"))
    return 0


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_ui() -> int:
    import webview

    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    port = free_port()

    def serve() -> None:
        try:
            app.run(host="127.0.0.1", port=port, debug=False, threaded=True,
                    use_reloader=False)
        except Exception:
            pass

    threading.Thread(target=serve, daemon=True).start()
    time.sleep(0.9)

    window = webview.create_window(
        f"卸载 {APP_DISPLAY}",
        f"http://127.0.0.1:{port}/",
        width=560,
        height=760,
        resizable=False,
        background_color="#f5f5f7",
        text_select=False,
    )
    STATE["window"] = window
    webview.start()
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--silent" in args:
        return silent_uninstall("--delete-data" not in args)
    return run_ui()


HTML = """
<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
  :root{
    --ink:#1d1d1f; --ink2:#6e6e73; --ink3:#8e8e93; --line:rgba(0,0,0,.08);
    --bg:#f5f5f7; --card:#fff; --blue:#007aff; --red:#d70015; --amber:#c76a00;
    --ease:cubic-bezier(.22,.61,.36,1);
  }
  *{box-sizing:border-box}
  body{
    margin:0;height:100vh;background:var(--bg);color:var(--ink);overflow:hidden;
    font:13px/1.55 "SF Pro Text",-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
    -webkit-font-smoothing:antialiased;display:flex;flex-direction:column;
  }
  header{display:flex;align-items:center;gap:12px;padding:22px 26px 16px}
  .mark{width:46px;height:46px;border-radius:12px;background:var(--red);flex:0 0 auto;
    display:flex;align-items:center;justify-content:center;box-shadow:0 8px 20px -8px rgba(215,0,21,.55)}
  .mark svg{width:22px;height:22px}
  header h1{margin:0;font-size:16px;font-weight:600;letter-spacing:-.01em}
  header p{margin:2px 0 0;font-size:11.5px;color:var(--ink2)}
  main{flex:1;padding:0 26px;overflow:auto}
  .pane{display:none;animation:fade .26s var(--ease)}
  .pane.on{display:block}
  @keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
  .card{background:var(--card);border-radius:12px;box-shadow:0 1px 2px rgba(0,0,0,.04),0 6px 18px -8px rgba(0,0,0,.12);padding:15px 18px;margin-bottom:12px}
  .card h2{margin:0 0 9px;font-size:12.5px;font-weight:600;color:var(--ink2)}
  .row{display:flex;justify-content:space-between;gap:12px;font-size:12.5px;padding:3px 0;color:var(--ink2)}
  .row b{color:var(--ink);font-weight:500}
  .mono{font-family:"Cascadia Mono",Consolas,monospace;font-size:11.5px;word-break:break-all;color:var(--ink)}
  .items{margin:0;padding:0;list-style:none}
  .items li{display:flex;justify-content:space-between;gap:10px;font-size:12.5px;color:var(--ink2);padding:3px 0}
  .items li span:last-child{color:var(--ink3);font-variant-numeric:tabular-nums}
  .empty{font-size:12.5px;color:var(--ink3)}
  label.opt{display:flex;gap:10px;align-items:flex-start;padding:11px 12px;border:1px solid var(--line);border-radius:10px;margin-bottom:8px;cursor:pointer;transition:border-color .16s,background .16s}
  label.opt:hover{background:#fafafa}
  label.opt input{margin:2px 0 0;width:15px;height:15px;accent-color:var(--blue);flex:0 0 auto}
  label.opt.danger input{accent-color:var(--red)}
  label.opt.on{border-color:var(--blue);background:rgba(0,122,255,.045)}
  label.opt.danger.on{border-color:var(--red);background:rgba(215,0,21,.045)}
  .opt-title{display:block;font-size:12.5px;font-weight:500;color:var(--ink)}
  .opt-desc{display:block;font-size:11.5px;color:var(--ink2);margin-top:3px;line-height:1.6}
  .warn{color:var(--amber);font-size:12px;margin:8px 0 0}
  .bad{color:var(--red);font-size:12px;margin:8px 0 0}
  footer{padding:14px 26px 20px;display:flex;align-items:center;gap:10px}
  footer .spacer{flex:1}
  .tip{font-size:11.5px;color:var(--ink3)}
  button{height:32px;padding:0 16px;border-radius:8px;border:1px solid var(--line);background:#fff;cursor:pointer;font:12.5px inherit;color:var(--ink);transition:background .16s,transform .1s}
  button:hover{background:#fafafa}
  button:active{transform:scale(.97)}
  button:disabled{opacity:.45;cursor:not-allowed}
  button.primary{background:var(--red);border-color:transparent;color:#fff;font-weight:500}
  button.primary:hover{background:#b80012}
  .spinner{width:16px;height:16px;border:2px solid rgba(0,0,0,.12);border-top-color:var(--blue);border-radius:50%;display:inline-block;vertical-align:-3px;margin-right:8px;animation:spin .8s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
</style></head>
<body>
  <header>
    <div class="mark">
      <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round">
        <path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M6 6l1 14h10l1-14"/>
      </svg>
    </div>
    <div>
      <h1>卸载 超星 AI 答题</h1>
      <p id="sub">正在读取安装信息…</p>
    </div>
  </header>

  <main>
    <section class="pane on" id="pane-confirm">
      <div class="card">
        <h2>安装位置</h2>
        <div class="mono" id="path">—</div>
        <div class="row" style="margin-top:8px"><span>程序占用</span><b id="program-size">—</b></div>
        <div class="row"><span>本地资料</span><b id="data-size">—</b></div>
      </div>

      <div class="card">
        <h2>检测到的本地资料</h2>
        <ul class="items" id="items"></ul>
        <p class="empty" id="no-items" hidden>没有发现本地资料。</p>
      </div>

      <div class="card">
        <h2>卸载方式</h2>
        <label class="opt on" id="opt-keep">
          <input type="radio" name="mode" value="keep" checked>
          <span>
            <span class="opt-title">只卸载程序，保留本地资料</span>
            <span class="opt-desc">账号、API Key、题库、配置都留在原目录，重新安装后可以直接接着用。</span>
          </span>
        </label>
        <label class="opt danger" id="opt-wipe">
          <input type="radio" name="mode" value="wipe">
          <span>
            <span class="opt-title">同时删除本地资料</span>
            <span class="opt-desc">程序、账号、API Key、题库、配置文件一并删除，无法恢复。</span>
          </span>
        </label>
      </div>
    </section>

    <section class="pane" id="pane-running">
      <div class="card">
        <h2>正在卸载</h2>
        <p style="margin:2px 0 0;font-size:12.5px;color:var(--ink2)">
          <span class="spinner"></span><span id="running-text">正在关闭程序并清理文件…</span>
        </p>
        <p class="warn" id="running-warn" hidden></p>
      </div>
    </section>

    <section class="pane" id="pane-invalid">
      <div class="card">
        <h2>无法卸载</h2>
        <p class="bad" id="invalid-text">这里不像安装目录，未做任何删除。</p>
      </div>
    </section>
  </main>

  <footer>
    <span class="tip" id="tip">卸载不会影响你的学习通账号本身</span>
    <span class="spacer"></span>
    <button id="cancel">取消</button>
    <button class="primary" id="go">卸载</button>
  </footer>

<script>
const $ = id => document.getElementById(id);
const PANES = ["pane-confirm", "pane-running", "pane-invalid"];
let mode = "keep";

function show(i) {
  PANES.forEach((id, idx) => $(id).classList.toggle("on", idx === i));
}

function setMode(value) {
  mode = value;
  $("opt-keep").classList.toggle("on", value === "keep");
  $("opt-wipe").classList.toggle("on", value === "wipe");
  $("go").textContent = value === "wipe" ? "删除并卸载" : "卸载";
  $("tip").textContent = value === "wipe"
    ? "账号、API Key、题库都会被删除，无法恢复"
    : "卸载不会影响你的学习通账号本身";
}

document.querySelectorAll('input[name="mode"]').forEach(input => {
  input.onchange = () => setMode(input.value);
});

async function load() {
  try {
    const d = await fetch("/api/info").then(r => r.json());
    $("path").textContent = d.path || "—";
    $("sub").textContent = d.version ? ("已安装 v" + d.version) : "未在系统里登记版本";
    $("program-size").textContent = d.program_size || "—";
    $("data-size").textContent = d.data_size || "0 B";

    const list = $("items");
    list.innerHTML = "";
    (d.items || []).forEach(item => {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = item.label + (item.is_dir ? "（WebView2 配置目录）" : "");
      const size = document.createElement("span");
      size.textContent = item.size;
      li.appendChild(name);
      li.appendChild(size);
      list.appendChild(li);
    });
    if (!(d.items || []).length) $("no-items").hidden = false;

    if (!d.valid) {
      $("invalid-text").textContent = "这个目录里没有 " + "超星AI答题.exe" + "，未做任何删除：\\n" + (d.path || "");
      $("go").disabled = true;
      show(2);
    }
  } catch (e) {
    $("sub").textContent = "读取安装信息失败";
  }
}

$("cancel").onclick = async () => {
  try { await fetch("/api/close", {method: "POST"}); } catch (e) {}
};

$("go").onclick = async () => {
  $("go").disabled = true;
  $("cancel").disabled = true;
  $("running-text").textContent = mode === "wipe"
    ? "正在删除程序与本地资料…" : "正在删除程序文件…";
  show(1);
  try {
    const r = await fetch("/api/uninstall", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({keep_data: mode === "keep"}),
    });
    const d = await r.json();
    if (d.error) {
      $("running-warn").hidden = false;
      $("running-warn").textContent = d.error;
      $("cancel").disabled = false;
      $("cancel").textContent = "关闭";
      return;
    }
    $("running-text").textContent = "正在删除文件，窗口即将关闭…";
    setTimeout(() => { fetch("/api/close", {method: "POST"}).catch(() => {}); }, 900);
  } catch (e) {
    $("running-warn").hidden = false;
    $("running-warn").textContent = "卸载失败：" + (e && e.message ? e.message : e);
    $("cancel").disabled = false;
    $("cancel").textContent = "关闭";
  }
};

load();
</script>
</body></html>
"""


if __name__ == "__main__":
    sys.exit(main())