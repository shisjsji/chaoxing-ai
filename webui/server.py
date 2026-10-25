"""超星 AI 答题控制台 —— 本地 Web 界面。

只负责三件事：写 config.ini、以子进程方式运行 main.py、把日志回传给浏览器。
不改动刷课主流程的业务逻辑，界面崩了也不会影响已启动的任务进程。
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from configparser import ConfigParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from flask import Flask, jsonify, render_template, request

from webui import secret

FROZEN = getattr(sys, "frozen", False)
ROOT = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent.parent
PYTHON = Path(sys.executable) if FROZEN else ROOT / ".venv" / "Scripts" / "python.exe"
CONFIG_PATH = ROOT / "config.ini"
SECRET_PATH = ROOT / "secret.dat"
RUNTIME_CONFIG = ROOT / ".runtime-config.ini"
DEEPSEEK_ENDPOINT = "https://api.deepseek.com"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
SEP = "\x1f"
CH_RE = re.compile(r"@@CH\|(.*?)@@(.*)$")
TQDM_RE = re.compile(r"(\d+)%\|[^|]*\|\s*([\d.]+[kKmM]?)/([\d.]+[kKmM]?)")
LOG_LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \| (\w+)\s*\| [^|]+? - (.*)$")
# 纯内部调试噪音, 对使用者没有信息量
NOISE_RE = re.compile(r"^(unfinished task|Progress logged|Task success|videoEl未加载)")
EVENT_PREFIX = "@@EVENT@@"
MAX_RUN_HOURS = 6
MAX_JOBS = 4
_CHAPTER_DEFAULT = {"name": "", "course": "", "pct": 0, "state": "pending",
                    "done": 0, "total": 0, "job": "", "log_count": 0,
                    "video_pct": 0, "video_cur": "", "video_total": ""}


def _template_path() -> Path:
    for candidate in (
        ROOT / "config_template.ini",
        Path(getattr(sys, "_MEIPASS", ROOT)) / "config_template.ini",
    ):
        if candidate.exists():
            return candidate
    return ROOT / "config_template.ini"

app = Flask(
    __name__,
    template_folder=str(Path(__file__).resolve().parent / "templates"),
)

_lock = threading.Lock()
# 更新进度：下载在后台线程跑，前端轮询这个状态画进度条
_update_state = {"stage": "idle", "percent": 0, "done": 0, "total": 0,
                 "message": "", "error": "", "version": ""}

# 内嵌考试浏览器：由桌面入口注入窗口对象，这里负责盯着它的地址栏
_exam_browser = {"window": None, "url": "", "enc": "", "answer_id": "", "exam_id": ""}
# 退出钩子：桌面入口注入，更新要重启时用它走正常的退出流程
_quit_hook = {"fn": None}
_state = {
    "proc": None,
    "logs": deque(maxlen=6000),
    "chapters": {},
    "seq": 0,
    "status": "idle",
    "exit_code": None,
    "started_at": None,
    "stopping": False,
    "exam": {
        "status": "idle",      # idle / running / waiting_face / done / error
        "name": "",
        "answered": 0,
        "submitted": False,    # done 时才有意义：是否真的交卷了
        "error": "",
        "face_ok": False,
        "driving": False,
    },
}


def _write_config(cfg: dict, path: Path | None = None, with_secrets: bool = False) -> None:
    parser = ConfigParser(interpolation=None)
    template = _template_path()
    if template.exists():
        parser.read(template, encoding="utf-8")
    for section in ("common", "tiku", "notification", "share"):
        if section not in parser:
            parser[section] = {}
    parser["common"].update(
        {
            "use_cookies": "false",
            "username": str(cfg.get("username", "")).strip(),
            "password": str(cfg.get("password", "")) if with_secrets else "",
            "course_list": ",".join(str(x) for x in (cfg.get("course_list") or [])),
            "speed": str(cfg.get("speed", 1)),
            "jobs": str(max(1, min(MAX_JOBS, int(cfg.get("jobs", 2) or 2)))),
            "notopen_action": cfg.get("notopen_action", "retry"),
            "retry_interval": "1.0",
            "work_redo_enabled": "true" if cfg.get("work_redo_enabled") else "false",
            "work_max_retries": str(cfg.get("work_max_retries", 3)),
            "add_learning_count": "false",
            "target_count": "100",
        }
    )
    parser["tiku"].update(
        {
            "provider": "AI",
            "check_llm_connection": "false",
            "submit": "true" if cfg.get("submit", True) else "false",
            "cover_rate": str(cfg.get("cover_rate", 0.9)),
            "delay": "0",
            "endpoint": _endpoint(cfg),
            "key": str(cfg.get("api_key", "")).strip() if with_secrets else "",
            "model": str(cfg.get("model", "deepseek-chat")).strip(),
            "vision_model": str(cfg.get("vision_model", "")).strip(),
            "reasoning_effort": str(cfg.get("reasoning_effort", "")).strip(),
            "min_interval_seconds": str(cfg.get("min_interval", 2)),
            "http_proxy": str(cfg.get("http_proxy", "")).strip(),
        }
    )
    parser["notification"]["provider"] = ""
    parser["share"].update({"enabled": "true"})
    target = path or CONFIG_PATH
    with open(target, "w", encoding="utf-8") as fp:
        parser.write(fp)


def _app_version() -> str:
    try:
        from api.updater import current_version

        return current_version()
    except Exception:
        return ""


def _num(value, default, cast=float):
    """config.ini 被手工改成非数字时不要让整个界面挂掉。"""
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


def _read_config() -> dict:
    if not CONFIG_PATH.exists():
        # 首次运行还没有配置文件时，也要把版本等基础信息给前端
        return {"version": _app_version(), "share_enabled": True,
                "password_saved": False, "has_key": False, "course_list": []}
    parser = ConfigParser(interpolation=None)
    parser.read(CONFIG_PATH, encoding="utf-8")
    common = parser["common"] if "common" in parser else {}
    tiku = parser["tiku"] if "tiku" in parser else {}
    saved_key = _saved_secrets().get("api_key", "")
    return {
        "username": common.get("username", ""),
        "password_saved": bool(_saved_secrets().get("password")),
        "course_list": [x for x in (common.get("course_list", "") or "").split(",") if x],
        "speed": _num(common.get("speed"), 1.0),
        "jobs": _num(common.get("jobs"), 4, int),
        "notopen_action": common.get("notopen_action", "retry"),
        "work_redo_enabled": common.get("work_redo_enabled", "false").lower() == "true",
        "submit": tiku.get("submit", "true").lower() == "true",
        "model": tiku.get("model", "deepseek-chat"),
        "vision_model": tiku.get("vision_model", ""),
        "reasoning_effort": tiku.get("reasoning_effort", ""),
        "endpoint": tiku.get("endpoint", DEEPSEEK_ENDPOINT),
        "api_key_tail": saved_key[-4:] if len(saved_key) >= 4 else "",
        "has_key": bool(saved_key),
        "share_enabled": True,
        "version": _app_version(),
    }


def _save_secret(password: str, api_key: str) -> None:
    payload = {
        "password": secret.protect(password) if password else "",
        "api_key": secret.protect(api_key) if api_key else "",
    }
    SECRET_PATH.write_text(json.dumps(payload), encoding="utf-8")


def _saved_secrets() -> dict:
    """读取本机加密保存的密码和 Key；没有加密文件时从旧版明文配置迁移一次。"""
    if SECRET_PATH.exists():
        try:
            data = json.loads(SECRET_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return {
            "password": secret.unprotect(data.get("password", "")),
            "api_key": secret.unprotect(data.get("api_key", "")),
        }

    if CONFIG_PATH.exists():
        parser = ConfigParser(interpolation=None)
        parser.read(CONFIG_PATH, encoding="utf-8")
        common = parser["common"] if "common" in parser else {}
        tiku = parser["tiku"] if "tiku" in parser else {}
        pwd = common.get("password", "") or ""
        key = tiku.get("key", "") or ""
        if pwd or key:
            _save_secret(pwd, key)
            return {"password": pwd, "api_key": key}
    return {}


def _fill_secrets(cfg: dict) -> dict:
    """界面留空的密码/Key 沿用上次保存的值，避免每次运行都要重填。"""
    saved = _saved_secrets()
    if not str(cfg.get("password", "")):
        cfg["password"] = saved.get("password", "")
    if not str(cfg.get("api_key", "")).strip():
        cfg["api_key"] = saved.get("api_key", "")
    return cfg


def _endpoint(cfg: dict) -> str:
    value = str(cfg.get("endpoint") or DEEPSEEK_ENDPOINT).strip().rstrip("/")
    return value or DEEPSEEK_ENDPOINT


def _key_of(course: str, chapter: str) -> str:
    return "%s%s%s" % (course, SEP, chapter) if course else chapter


# ---------- 内嵌考试浏览器 ----------
def set_exam_window(window) -> None:
    """桌面入口创建好第二个窗口后交过来。"""
    _exam_browser["window"] = window


def set_quit_hook(fn) -> None:
    """桌面入口把「按正常流程退出」的函数交过来。"""
    _quit_hook["fn"] = fn


def quit_for_update() -> None:
    """更新前自己退出，而不是等更新脚本 Force 硬杀。

    硬杀会让 msedgewebview2 变成孤儿，占着 WebView2 的配置目录不放手，
    下次启动就得排队等它 —— 表现就是开头几秒黑屏。这里走正常退出：
    先关考场窗口，再让桌面入口的 quit_app 关主窗口，进程自然结束。
    """

    def run() -> None:
        # 先让 /api/update/restart 的响应发出去，再动窗口
        time.sleep(0.6)
        try:
            win = _exam_browser.get("window")
            if win is not None:
                win.destroy()
        except Exception:
            pass
        fn = _quit_hook.get("fn")
        if fn is not None:
            try:
                fn()
            except Exception:
                pass
        # 窗口关完 webview.start() 会返回、进程自己退；这里是兜底
        time.sleep(3.0)
        os._exit(0)

    threading.Thread(target=run, daemon=True).start()


def _watch_exam_browser() -> None:
    """盯着内嵌浏览器的地址栏。

    进考场必须过图形验证码，这一步交给真浏览器；程序只从地址里取 enc 等参数，
    拿到就能直接逐题答题了。
    """
    while True:
        time.sleep(1)
        win = _exam_browser.get("window")
        if win is None:
            continue
        try:
            url = win.get_current_url() or ""
        except Exception:
            continue
        query = parse_qs(urlparse(url).query)
        enc = (query.get("enc") or [""])[0]
        answer_id = (query.get("id") or [""])[0]
        exam_id = (query.get("tId") or [""])[0]
        with _lock:
            _exam_browser["url"] = url
            if enc:
                _exam_browser["enc"] = enc
                _exam_browser["answer_id"] = answer_id
                _exam_browser["exam_id"] = exam_id


threading.Thread(target=_watch_exam_browser, daemon=True).start()


@app.post("/api/exam/browser")
def api_exam_browser_open():
    """在内嵌浏览器里打开考试入口（验证码和签名都交给浏览器）。"""
    data = request.get_json(force=True, silent=True) or {}
    url = str(data.get("url") or "").strip()
    win = _exam_browser.get("window")
    if win is None:
        return jsonify(ok=False,
                       error="内嵌浏览器不可用，请在浏览器里手动进入考试后点「接管」"), 400
    with _lock:
        _exam_browser.update(url="", enc="", answer_id="", exam_id="")
    try:
        win.load_url(url or "https://mooc1.chaoxing.com/")
        win.show()
    except Exception as exc:
        return jsonify(ok=False, error=f"打不开浏览器窗口：{exc}"), 500
    return jsonify(ok=True, message="已打开考试窗口，请在里面进入考场")


@app.get("/api/exam/browser")
def api_exam_browser_state():
    with _lock:
        return jsonify(ok=True, url=_exam_browser["url"], enc=_exam_browser["enc"],
                       answer_id=_exam_browser["answer_id"],
                       exam_id=_exam_browser["exam_id"])


@app.post("/api/exam/autodrive")
def api_exam_autodrive():
    """在考试窗口里自动作答，按答题卡复查补漏。"""
    win = _exam_browser.get("window")
    if win is None:
        return jsonify(ok=False, error="内嵌浏览器不可用"), 400
    with _lock:
        if _state["exam"].get("driving"):
            return jsonify(ok=False, error="已经在自动作答了"), 409
        if _state["proc"] is not None:
            return jsonify(ok=False, error="刷课任务正在运行，请先停止再开始考试"), 409
        _state["exam"].update(driving=True, status="running", answered=0, error="")

    threading.Thread(target=_drive_exam, args=(win,), daemon=True).start()
    return jsonify(ok=True, message="已开始自动作答")


def _drive_exam(win) -> None:
    from api.browser_exam import run_pass

    def log(msg: str) -> None:
        _append_log(msg)

    try:
        started = time.time()
        log("开始在考试窗口里自动作答")
        stats = run_pass(win, log)
        elapsed = int(time.time() - started)
        with _lock:
            _state["exam"].update(answered=stats.get("ok", 0),
                                  submitted=bool(stats.get("submitted")))
        log(f"自动作答结束（{elapsed} 秒）：作答 {stats.get('ok', 0)} 题，"
            f"未完成 {stats.get('fail', 0)} 题，剩余未答 {stats.get('left', 0)} 题")
        if stats.get("submitted"):
            log("已自动交卷")
        elif stats.get("left", 0) == 0:
            log("全部题目已作答，但自动交卷没成功——请在考试窗口里手动点交卷")
    except Exception as exc:
        with _lock:
            _state["exam"].update(error=f"{type(exc).__name__}: {exc}")
        log(f"自动作答中断：{type(exc).__name__}: {exc}")
        try:
            from api.feedback import push_error

            push_error("exam.autodrive", exc, f"elapsed={int(time.time() - started)}s")
        except Exception:
            pass
    finally:
        with _lock:
            _state["exam"].update(driving=False, status="done")



def _append_log(text: str, key: str = "", course: str = "", chapter: str = "",
                kind: str = "", level: str = "") -> None:
    with _lock:
        if kind == "progress" and key:
            # 每章节只保留最新一条进度, 否则每秒一条会把真正的日志挤出去
            for old in [e for e in _state["logs"]
                        if e.get("kind") == "progress" and e.get("ch") == key]:
                try:
                    _state["logs"].remove(old)
                except ValueError:
                    pass
        _state["seq"] += 1
        entry = {"n": _state["seq"], "t": time.strftime("%H:%M:%S"), "m": text, "ch": key}
        if kind:
            entry["kind"] = kind
        if level:
            entry["lv"] = level
        _state["logs"].append(entry)
        if key:
            info = _state["chapters"].setdefault(key, dict(_CHAPTER_DEFAULT))
            info["name"] = chapter
            info["course"] = course
            info["log_count"] += 1


def _apply_event(data: dict) -> None:
    kind = data.get("type")
    if kind not in ("chapter", "video_confirm"):
        return
    name = str(data.get("chapter") or "未命名章节")
    course = str(data.get("course") or "")
    key = _key_of(course, name)
    with _lock:
        info = _state["chapters"].setdefault(key, dict(_CHAPTER_DEFAULT))
        info["name"] = name
        info["course"] = course
        if kind == "chapter":
            if data.get("state"):
                info["state"] = str(data["state"])
            for key in ("pct", "done", "total"):
                if key in data:
                    info[key] = int(data[key])
            if data.get("job"):
                info["job"] = str(data["job"])
            if data.get("state") in ("done", "error", "notopen"):
                info["video_pct"] = 0
                info["video_cur"] = ""
                info["video_total"] = ""
        else:
            # 只在还没到 100% 时补满, 避免进度条来回跳
            if info["pct"] < 100:
                info["pct"] = 100
            info["state"] = "confirming"
            if data.get("name"):
                info["job"] = str(data["name"])


def _pump(proc: subprocess.Popen) -> None:
    decoder = json.JSONDecoder()
    try:
        for raw in proc.stdout:
            for part in raw.replace("\r", "\n").split("\n"):
                line = ANSI_RE.sub("", part).strip()
                if not line:
                    continue

                # 事件行可能与日志粘在同一行（stdout 与 loguru 异步 sink 交错输出），
                # 所以不能只判断行首，要按标记切开分别处理
                event_raw = ""
                if EVENT_PREFIX in line:
                    head, _, event_raw = line.partition(EVENT_PREFIX)
                    line = head.strip()

                course = ""
                chapter = ""
                matched = CH_RE.search(line)
                if matched:
                    head = matched.group(1)
                    course, _, chapter = head.partition(SEP)
                    # 只摘掉标记本身, 后面的消息必须保留
                    line = (line[:matched.start()] + matched.group(2)).strip()

                key = _key_of(course, chapter)
                if line:
                    if "%|" in line:
                        # tqdm 进度条：更新章节的视频进度，并在日志里原地刷新一行
                        prog = TQDM_RE.search(line)
                        if prog:
                            pct, cur, total = prog.group(1), prog.group(2), prog.group(3)
                            with _lock:
                                info = _state["chapters"].get(key)
                                if info is not None:
                                    info["video_pct"] = int(pct)
                                    info["video_cur"] = cur
                                    info["video_total"] = total
                            # 原样保留 tqdm 进度条: 任务名、进度块、当前/总时长、速率都在里面
                            _append_log(line, key, course, chapter, kind="progress")
                    else:
                        level = ""
                        parsed = LOG_LINE_RE.match(line)
                        if parsed:
                            level = parsed.group(1).upper()
                            line = parsed.group(2).strip()
                        if line and not NOISE_RE.match(line):
                            _append_log(line, key, course, chapter, level=level)

                if event_raw:
                    # 只解析第一个 JSON 对象，忽略其后可能粘上的内容
                    try:
                        payload, _ = decoder.raw_decode(event_raw.strip())
                    except ValueError:
                        payload = None
                    if isinstance(payload, dict):
                        _apply_event(payload)
    finally:
        code = proc.wait()
        with _lock:
            if _state["proc"] is proc:
                _state["proc"] = None
                _state["exit_code"] = code
                if _state.get("stopping"):
                    _state["status"] = "stopped"
                    _state["stopping"] = False
                else:
                    _state["status"] = "finished" if code == 0 else "error"


def _validate(cfg: dict) -> str | None:
    if not str(cfg.get("username", "")).strip():
        return "请填写手机号"
    if not str(cfg.get("password", "")):
        return "请填写密码"
    if not str(cfg.get("api_key", "")).strip():
        return "请填写 DeepSeek API Key"
    if not FROZEN and not (ROOT / "main.py").exists():
        return "找不到 main.py"
    if not PYTHON.exists():
        return "找不到 .venv 解释器，请先创建虚拟环境"
    return None


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/config")
def api_get_config():
    return jsonify(_read_config())


@app.post("/api/config")
def api_save_config():
    cfg = _fill_secrets(request.get_json(force=True, silent=True) or {})
    err = _validate(cfg)
    if err:
        return jsonify(ok=False, error=err), 400
    _save_secret(cfg.get("password", ""), cfg.get("api_key", ""))
    _write_config(cfg)
    note = "密码与 Key 已加密保存在本机" if secret.available() else "（当前系统不支持加密，密码以原文保存）"
    return jsonify(ok=True, message="配置已保存，" + note)


@app.post("/api/test-llm")
def api_test_llm():
    cfg = _fill_secrets(request.get_json(force=True, silent=True) or {})
    key = str(cfg.get("api_key", "")).strip()
    model = str(cfg.get("model", "deepseek-chat")).strip() or "deepseek-chat"
    if not key:
        return jsonify(ok=False, error="请先填写 API Key"), 400
    try:
        from openai import OpenAI

        client = OpenAI(base_url=_endpoint(cfg), api_key=key, timeout=30)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "1+1等于几？只回答数字"}],
            max_tokens=16,
        )
        reply = (resp.choices[0].message.content or "").strip()
        return jsonify(ok=True, reply=reply[:60], message=f"{model} 连接正常，返回：{reply[:20]}")
    except Exception as exc:
        return jsonify(ok=False, error=f"{type(exc).__name__}: {exc}")


@app.post("/api/exams")
def api_exams():
    """列出所有课程的考试。"""
    cfg = _fill_secrets(request.get_json(force=True, silent=True) or {})
    username = str(cfg.get("username", "")).strip()
    password = str(cfg.get("password", ""))
    if not username or not password:
        return jsonify(ok=False, error="请先在设置里填写学习通账号和密码"), 400
    try:
        from api.base import Account, Chaoxing, SessionManager
        from api.answer import DummyTiku
        from api.exam import list_exams

        chaoxing = Chaoxing(
            account=Account(username, password),
            tiku=DummyTiku(),
            query_delay=0,
            work_max_retries=0,
            work_redo_enabled=False,
        )
        state = chaoxing.login(login_with_cookies=False)
        if not state.get("status"):
            return jsonify(ok=False, error=f"登录失败：{state.get('msg')}"), 400

        session = SessionManager.get_session()
        courses = chaoxing.get_course_list()
        result = []
        for course in courses:
            try:
                items = list_exams(session, course)
            except Exception:
                items = []
            if items:
                result.append({
                    "course": course.get("title", ""),
                    "courseId": str(course.get("courseId", "")),
                    "clazzId": str(course.get("clazzId", "")),
                    "cpi": str(course.get("cpi", "")),
                    "items": [
                        {"exam_id": it.exam_id, "enc_task": it.enc_task, "name": it.name,
                         "status": it.status, "expire": it.expire_time}
                        for it in items
                    ],
                })
        total = sum(len(c["items"]) for c in result)
        return jsonify(ok=True, courses=result, total=total)
    except Exception as exc:
        return jsonify(ok=False, error=f"{type(exc).__name__}: {exc}"), 500


@app.post("/api/exam/run")
def api_exam_run():
    """已废弃：HTTP 直连考试走不通（验证码 + 选项打乱 + 无效操作）。

    保留路由是为了给旧调用一个明确答复，不要让请求静默走进死路径。
    """
    return jsonify(ok=False,
                   error="这条通道已废弃：请用考试页的「开始」按钮，走内嵌浏览器作答"), 410


@app.post("/api/exam/face_done")
def api_exam_face_done():
    """使用者确认已自行完成人脸验证。"""
    with _lock:
        if _state["exam"]["status"] != "waiting_face":
            return jsonify(ok=False, error="当前不在等待人脸验证"), 400
        _state["exam"]["face_ok"] = True
    _append_log("已确认完成人脸验证，继续答题")
    return jsonify(ok=True)


@app.get("/api/exam/status")
def api_exam_status():
    with _lock:
        return jsonify(dict(_state["exam"]))


@app.post("/api/feedback/push")
def api_feedback_push():
    """前端提交反馈，或把最近的日志一并发上来。"""
    data = request.get_json(force=True, silent=True) or {}
    message = str(data.get("message", "")).strip()
    context = str(data.get("context", "")).strip()
    kind = str(data.get("kind", "feedback")).strip() or "feedback"
    module = str(data.get("module", "ui")).strip() or "ui"
    if not message and not context:
        return jsonify(ok=False, error="内容为空"), 400
    try:
        from api.feedback import push

        ok = push(kind, module, message, context)
        if not ok:
            return jsonify(ok=False, error="发送失败，请检查网络后重试"), 502
        return jsonify(ok=True, message="已发送，感谢反馈")
    except Exception as exc:
        return jsonify(ok=False, error=f"{type(exc).__name__}: {exc}"), 500


@app.get("/api/update/check")
def api_update_check():
    try:
        from api.remote_library import _conf as share_conf
        from api.updater import changelog, check_update, current_version

        cfg = share_conf()
        result = check_update(cfg.get("endpoint", ""), cfg.get("token", ""))
        result["current"] = current_version()
        result.setdefault("current_changelog", changelog())
        return jsonify(result)
    except Exception as exc:
        return jsonify(ok=False, error=f"{type(exc).__name__}: {exc}"), 500


@app.post("/api/update/apply")
def api_update_apply():
    """启动更新。立刻返回，进度由 /api/update/progress 查询。"""
    data = request.get_json(force=True, silent=True) or {}
    url = str(data.get("url", "")).strip()
    sha = str(data.get("sha256", "")).strip()
    version = str(data.get("version", "")).strip()
    if not url:
        return jsonify(ok=False, error="缺少下载地址"), 400

    with _lock:
        if _update_state["stage"] in ("downloading", "verifying", "preparing", "restarting"):
            return jsonify(ok=False, error="更新已经开始了，请等它跑完"), 409
        _update_state.update(stage="downloading", percent=0, done=0, total=0,
                             message="正在连接下载服务器", error="", version=version)

    threading.Thread(target=_run_update, args=(url, sha, version), daemon=True).start()
    return jsonify(ok=True, message="已开始下载")


def _run_update(url: str, sha: str, version: str) -> None:
    """后台下载并准备替换。每一步都更新 _update_state 供前端显示。"""
    from api.updater import apply_and_restart, download

    def progress(done_bytes: int, total_bytes: int) -> None:
        percent = int(done_bytes * 100 / total_bytes) if total_bytes else 0
        mb_done = done_bytes / 1024 / 1024
        if total_bytes:
            message = f"已下载 {mb_done:.1f} / {total_bytes / 1024 / 1024:.0f} MB"
        else:
            message = f"已下载 {mb_done:.1f} MB"
        with _lock:
            _update_state.update(percent=percent, done=done_bytes,
                                 total=total_bytes, message=message)

    try:
        path = download(url, sha, progress=progress)
        with _lock:
            _update_state.update(stage="verifying", percent=100, message="校验通过")
        from api.updater import save_pending

        save_pending(path, version, sha)
        with _lock:
            # 停在这里等用户决定，不自动重启
            _update_state.update(stage="ready", percent=100,
                                 message="已下载完成，点「立即重启」安装")
    except Exception as exc:
        with _lock:
            _update_state.update(stage="error",
                                 error=f"{type(exc).__name__}: {exc}",
                                 message="更新失败，已保留当前版本")
        _append_log(f"更新失败：{type(exc).__name__}: {exc}")
        try:
            from api.feedback import push_error

            push_error("update.apply", exc, f"version={version}")
        except Exception:
            pass


@app.post("/api/update/restart")
def api_update_restart():
    """用户点「立即重启」后才真正替换并重启。"""
    from api.updater import apply_and_restart, load_pending

    pending = load_pending()
    if not pending:
        with _lock:
            _update_state.update(stage="idle", percent=0, message="", error="")
        return jsonify(ok=False, error="没有已下载的更新包，请重新检查更新"), 400

    with _lock:
        if _update_state["stage"] == "restarting":
            return jsonify(ok=False, error="已经在重启了"), 409
        _update_state.update(stage="restarting", percent=100, message="正在退出并安装，稍等")

    try:
        restarted = apply_and_restart(pending["zip"])
    except Exception as exc:
        with _lock:
            _update_state.update(stage="error", error=f"{type(exc).__name__}: {exc}",
                                 message="安装启动失败，已保留当前版本")
        return jsonify(ok=False, error=f"{type(exc).__name__}: {exc}"), 500

    if not restarted:
        with _lock:
            _update_state.update(stage="ready", percent=100,
                                 message="开发模式下不自动替换，更新包已就绪")
        return jsonify(ok=False, error="开发模式下不执行替换"), 400
    quit_for_update()
    return jsonify(ok=True, message="正在重启安装")


@app.get("/api/update/progress")
def api_update_progress():
    with _lock:
        state = dict(_update_state)
    # 进程重启过：内存状态丢了，但磁盘上可能还有已下载的包
    if state.get("stage") in ("idle", ""):
        from api.updater import load_pending

        pending = load_pending()
        if pending:
            state.update(stage="ready", percent=100,
                         message=f"更新包已就绪（v{pending.get('version', '')}），点「立即重启」安装",
                         version=pending.get("version", ""))
    return jsonify(state)


@app.get("/api/library")
def api_library():
    keyword = request.args.get("q", "")
    try:
        from api.library import Library

        lib = Library()
        return jsonify(ok=True, stats=lib.stats(), items=lib.search(keyword))
    except Exception as exc:
        return jsonify(ok=False, error=f"{type(exc).__name__}: {exc}"), 500


@app.get("/api/library/remote")
def api_library_remote():
    keyword = request.args.get("q", "")
    try:
        from api.remote_library import list_remote

        data = list_remote(60, keyword)
        if not data.get("ok"):
            return jsonify(ok=False, error=data.get("error", "服务器题库不可用")), 200
        return jsonify(ok=True, total=data.get("total", 0), items=data.get("items", []))
    except Exception as exc:
        return jsonify(ok=False, error=f"{type(exc).__name__}: {exc}"), 500


@app.post("/api/library/delete")
def api_library_delete():
    data = request.get_json(force=True, silent=True) or {}
    fp = str(data.get("fingerprint", "")).strip()
    if not fp:
        return jsonify(ok=False, error="缺少 fingerprint"), 400
    try:
        from api.library import Library

        Library().delete(fp)
        return jsonify(ok=True, message="已删除")
    except Exception as exc:
        return jsonify(ok=False, error=f"{type(exc).__name__}: {exc}"), 500


@app.post("/api/models")
def api_models():
    cfg = _fill_secrets(request.get_json(force=True, silent=True) or {})
    key = str(cfg.get("api_key", "")).strip()
    if not key:
        return jsonify(ok=False, error="请先填写 API Key"), 400
    base = _endpoint(cfg)
    try:
        from openai import OpenAI

        client = OpenAI(base_url=base, api_key=key, timeout=25)
        listed = client.models.list()
        ids = sorted({m.id for m in listed.data})
        if not ids:
            return jsonify(ok=False, error="接口返回了空模型列表"), 400
        return jsonify(ok=True, models=ids, endpoint=base)
    except Exception as exc:
        return jsonify(ok=False, error=f"{type(exc).__name__}: {exc}")


@app.post("/api/courses")
def api_courses():
    cfg = _fill_secrets(request.get_json(force=True, silent=True) or {})
    username = str(cfg.get("username", "")).strip()
    password = str(cfg.get("password", ""))
    if not username or not password:
        return jsonify(ok=False, error="请先填写手机号和密码"), 400
    try:
        from api.answer import DummyTiku
        from api.base import Account, Chaoxing

        chaoxing = Chaoxing(
            account=Account(username, password),
            tiku=DummyTiku(),
            query_delay=0,
            work_max_retries=0,
            work_redo_enabled=False,
        )
        state = chaoxing.login(login_with_cookies=False)
        if not state.get("status"):
            return jsonify(ok=False, error=f"登录失败：{state.get('msg')}"), 400
        courses = chaoxing.get_course_list()
        out = [
            {"id": str(c.get("courseId")), "title": c.get("title", "")}
            for c in courses
        ]
        return jsonify(ok=True, courses=out, message=f"拉取到 {len(out)} 门课程")
    except Exception as exc:
        return jsonify(ok=False, error=f"{type(exc).__name__}: {exc}"), 500


@app.post("/api/start")
def api_start():
    cfg = _fill_secrets(request.get_json(force=True, silent=True) or {})
    with _lock:
        if _state["proc"] is not None:
            return jsonify(ok=False, error="已有任务在运行，请先停止"), 409
    err = _validate(cfg)
    if err:
        return jsonify(ok=False, error=err), 400
    _save_secret(cfg.get("password", ""), cfg.get("api_key", ""))
    _write_config(cfg)
    _write_config(cfg, path=RUNTIME_CONFIG, with_secrets=True)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["CHAOXING_WEBUI"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    if FROZEN:
        cmd = [sys.executable, "--run-task", "-c", str(RUNTIME_CONFIG)]
    else:
        cmd = [str(PYTHON), str(ROOT / "main.py"), "-c", str(RUNTIME_CONFIG)]
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    with _lock:
        _state["proc"] = proc
        _state["stopping"] = False
        _state["logs"] = deque(maxlen=6000)
        _state["chapters"] = {}
        _state["seq"] = 0
        _state["status"] = "running"
        _state["exit_code"] = None
        _state["started_at"] = time.time()
    threading.Thread(target=_pump, args=(proc,), daemon=True).start()
    threading.Thread(target=_watchdog, args=(proc,), daemon=True).start()
    # 子进程启动时会读取配置，稍后删除临时文件，避免明文密码长期留在磁盘上
    threading.Timer(5.0, _cleanup_runtime_config).start()
    return jsonify(ok=True, pid=proc.pid)


_EXAM_FACE_TIMEOUT = 300


def _run_exam_thread(username, password, course, exam_id, enc_task, name,
                     attach=False, retake=False, enc="", answer_id="") -> None:
    """在后台跑一场考试；日志写进前端可见的通道。"""
    import time as _time

    def log(msg: str) -> None:
        _append_log(msg)

    def wait_face(_sess) -> bool:
        with _lock:
            _state["exam"]["status"] = "waiting_face"
        log("本场考试需要人脸验证，请自行完成后再点「我已完成验证」")
        for _ in range(_EXAM_FACE_TIMEOUT):
            with _lock:
                if _state["exam"]["face_ok"]:
                    return True
                if _state["exam"]["status"] != "waiting_face":
                    return False
            _time.sleep(1)
        log("等待人脸验证超时，已停止")
        return False

    try:
        from api.answer import DummyTiku
        from api.base import Account, Chaoxing, SessionManager
        from api.exam import run_exam

        chaoxing = Chaoxing(
            account=Account(username, password),
            tiku=DummyTiku(),
            query_delay=0,
            work_max_retries=0,
            work_redo_enabled=False,
        )
        state = chaoxing.login(login_with_cookies=False)
        if not state.get("status"):
            raise RuntimeError(f"登录失败：{state.get('msg')}")

        session = SessionManager.get_session()
        def progress(answered, seen):
            with _lock:
                _state["exam"]["answered"] = answered

        def finish(submitted):
            with _lock:
                _state["exam"]["submitted"] = bool(submitted)

        count = run_exam(session, course, exam_id, enc_task, log=log,
                         wait_face=wait_face, on_progress=progress, on_finish=finish,
                         attach=attach, retake=retake, enc=enc, answer_id=answer_id)
        with _lock:
            _state["exam"].update(status="done", answered=count)
    except Exception as exc:
        with _lock:
            _state["exam"].update(status="error", error=f"{type(exc).__name__}: {exc}")
        log(f"考试失败：{type(exc).__name__}: {exc}")
    finally:
        # 无论成功失败都要放开，否则「自动作答」按钮会一直被挡住
        with _lock:
            _state["exam"]["driving"] = False


def _watchdog(proc: subprocess.Popen) -> None:
    """运行时长超过上限时强制结束，避免无人值守时无限跑。"""
    limit = MAX_RUN_HOURS * 3600
    started = time.time()
    while proc.poll() is None:
        if time.time() - started > limit:
            _append_log(f"运行时长超过 {MAX_RUN_HOURS} 小时上限, 正在停止任务")
            with _lock:
                _state["stopping"] = True
            _kill_tree(proc)
            return
        time.sleep(30)


def _cleanup_runtime_config() -> None:
    try:
        if RUNTIME_CONFIG.exists():
            RUNTIME_CONFIG.unlink()
    except Exception:
        pass


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, text=True)
        else:
            import signal

            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


@app.post("/api/stop")
def api_stop():
    with _lock:
        proc = _state["proc"]
        if proc is not None:
            _state["stopping"] = True
    if proc is None:
        return jsonify(ok=False, error="当前没有运行中的任务"), 400
    _kill_tree(proc)
    return jsonify(ok=True, message="已发送停止指令")


@app.get("/api/logs")
def api_logs():
    try:
        since = int(request.args.get("since", 0))
    except ValueError:
        since = 0
    with _lock:
        items = [x for x in _state["logs"] if x["n"] > since]
        chapters = [dict(key=k, **v) for k, v in _state["chapters"].items()]
        status = _state["status"]
        exit_code = _state["exit_code"]
    return jsonify(items=items, status=status, exit_code=exit_code, chapters=chapters)


@app.get("/api/status")
def api_status():
    with _lock:
        return jsonify(
            status=_state["status"],
            exit_code=_state["exit_code"],
            running=_state["proc"] is not None,
            log_count=_state["seq"],
        )