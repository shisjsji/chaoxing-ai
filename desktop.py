"""桌面应用入口：内嵌 WebView 窗口 + 系统托盘。

关闭窗口不会退出程序，而是收进托盘继续在后台运行；从托盘可以重新打开或彻底退出。
开发环境：.venv\\Scripts\\python.exe desktop.py
带 --run-task 时进入任务子进程模式。
"""

import ctypes
import os
import socket
import subprocess
import sys
import threading
import time

HOST, PORT = "127.0.0.1", 5000
URL = f"http://{HOST}:{PORT}"


# 只允许开一个界面进程。用文件锁而不是命名互斥量：互斥量要过 ctypes
# 的参数转换，打包后拿不准；文件锁由系统在内核里持有，进程被杀也会自动释放，
# 不会留下需要手工清理的残留。
LOCK_FILE = "ui.lock"
_LOCK_HANDLE = []


def already_running() -> bool:
    """已经开着一个就返回 True。锁文件句柄留在 _LOCK_HANDLE 里，退出才会释放。"""
    try:
        import msvcrt

        path = os.path.join(base_dir(), LOCK_FILE)
        fp = open(path, "a+")
        try:
            msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            fp.close()
            return True
        _LOCK_HANDLE.append(fp)
        return False
    except Exception:
        # 拿不到锁文件也不能拦住启动，宁可开两个也别打不开
        return False


def focus_existing() -> bool:
    """把已经开着的窗口提到前台。找不到（比如是个没窗口的僵尸进程）返回 False。"""
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_cb(hwnd, _):
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value.strip() == "超星 AI 答题" and ctypes.windll.user32.IsWindowVisible(hwnd):
                found.append(hwnd)
        return True

    try:
        ctypes.windll.user32.EnumWindows(enum_cb, 0)
        if not found:
            return False
        hwnd = found[0]
        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def notify(text: str, title: str = "超星 AI 答题") -> None:
    """窗口还没建起来时只能走系统消息框。"""
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    except Exception:
        pass


def base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def port_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.6)
        return sock.connect_ex((HOST, PORT)) == 0


def run_server() -> None:
    from webui.server import app

    app.run(host=HOST, port=PORT, debug=False, threaded=True)


def wait_ready(timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_in_use():
            return True
        time.sleep(0.15)
    return False


def run_task() -> None:
    sys.argv = [a for a in sys.argv if a != "--run-task"]
    os.chdir(base_dir())
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    import main as chaoxing_main

    chaoxing_main.main()


def tray_image():
    """程序内生成托盘图标，避免依赖外部文件。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([2, 2, 61, 61], radius=14, fill=(0, 122, 255, 255))
    draw.arc([18, 18, 46, 46], start=-90, end=200, fill=(255, 255, 255, 255), width=4)
    draw.ellipse([29, 29, 35, 35], fill=(255, 255, 255, 255))
    return img


LOADING_HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>
html,body{height:100%;margin:0;background:#f6f7f9;color:#57606a;
font:13px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{height:100%;display:flex;align-items:center;justify-content:center}
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#2563eb;
margin-right:7px;animation:p 1s ease-in-out infinite}
@keyframes p{0%,100%{opacity:.25}50%{opacity:1}}
</style></head><body><div class="wrap">
<span class="dot"></span>正在启动，请稍候…</div></body></html>"""


def launch_window() -> None:
    import webview

    window = webview.create_window(
        "超星 AI 答题",
        html=LOADING_HTML,
        width=1180,
        height=840,
        min_size=(980, 680),
        background_color="#f2f2f7",
        text_select=True,
    )

    # 考试专用窗口：进考场要过图形验证码和客户端签名，交给真浏览器最稳，
    # 程序只从它的地址栏里取 enc 然后自动答题。
    # 无边框是为了嵌进界面的「考试」页 —— 有标题栏就露馅了。
    exam = webview.create_window(
        "考试（在此进入考场）",
        "about:blank",
        width=1080,
        height=820,
        hidden=True,
        frameless=True,
        text_select=True,
    )
    from webui.server import set_exam_window

    set_exam_window(exam)

    def _embed_exam() -> None:
        """等两个窗口的原生句柄都就绪，再把考场窗口挂进主窗口。"""
        from api import exam_embed

        for _ in range(80):
            time.sleep(0.3)
            try:
                if exam_embed.attach(window, exam):
                    return
            except Exception:
                pass
        # 挂不进去就还原成普通窗口，否则无边框又关不掉
        try:
            exam_embed.make_standalone(exam)
        except Exception:
            pass

    threading.Thread(target=_embed_exam, daemon=True).start()

    # 先显示启动提示，等本地服务真的能响应再加载界面，
    # 避免 WebView2 冷启动时长时间黑屏
    def _goto_ui() -> None:
        if wait_ready(timeout=30):
            for _ in range(20):
                try:
                    window.load_url(URL)
                    return
                except Exception:
                    time.sleep(0.5)

    threading.Thread(target=_goto_ui, daemon=True).start()

    state = {"tray": None, "quitting": False}

    def quit_app(*_args) -> None:
        state["quitting"] = True
        try:
            from webui.server import append_ui_log

            append_ui_log("界面退出")
        except Exception:
            pass
        try:
            if state["tray"] is not None:
                state["tray"].stop()
        except Exception:
            pass
        try:
            from api import exam_embed

            exam_embed.detach()
        except Exception:
            pass
        try:
            exam.destroy()
        except Exception:
            pass
        try:
            window.destroy()
        except Exception:
            pass
        # 兜底：窗口关不掉时进程会残留，托盘已经没了却还占着单实例锁，
        # 下次启动就变成「提示已经在运行但找不到窗口」。3 秒后自己收尾。
        def _force_exit() -> None:
            time.sleep(3.0)
            os._exit(0)

        threading.Thread(target=_force_exit, daemon=True).start()

    def on_closing():
        # 关窗口只是收进托盘，程序继续在后台跑
        if state["quitting"]:
            return True
        try:
            window.hide()
        except Exception:
            pass
        return False

    try:
        window.events.closing += on_closing
    except Exception:
        pass

    # 更新要重启时由服务端调用，走正常退出（不发 Force 杀）
    try:
        from webui.server import set_quit_hook

        set_quit_hook(quit_app)
    except Exception:
        pass

    def setup_tray() -> None:
        try:
            import pystray

            def show_window(icon=None, item=None):
                try:
                    window.show()
                    window.restore()
                except Exception:
                    pass

            menu = pystray.Menu(
                pystray.MenuItem("显示窗口", show_window, default=True),
                pystray.MenuItem("在浏览器中打开", lambda *_: _open_browser()),
                pystray.MenuItem("退出", quit_app),
            )
            icon = pystray.Icon("chaoxing-ai", tray_image(), "超星 AI 答题", menu)
            state["tray"] = icon
            icon.run()
        except Exception:
            pass

    threading.Thread(target=setup_tray, daemon=True).start()

    # 固定 WebView2 配置目录。不给的话 pywebview 默认 private_mode=True，
    # 每次启动都在 %TEMP% 新建一份配置，等于每次冷启动（实测多等 2.6 秒），
    # 还会在临时目录里堆下几百 MB 的残留。固定后启动快一半，考场登录态也能留住。
    storage = os.path.join(
        os.environ.get("LOCALAPPDATA") or base_dir(), "超星AI答题", "webview"
    )
    try:
        webview.start(private_mode=False, storage_path=storage)
    except Exception:
        webview.start()


def _open_browser() -> None:
    import webbrowser

    webbrowser.open(URL)


def main() -> None:
    os.chdir(base_dir())

    from api.updater import pending_failure, update_in_progress

    # 更新脚本正在换文件：这时候再开一个会把 _internal 占住，替换直接失败
    if update_in_progress():
        notify("正在安装更新，请等它自己打开（一般十几秒）。\n\n"
               "现在手动打开会打断替换，更新会前功尽弃。")
        return

    if already_running():
        try:
            from api.logger import logger

            logger.info("检测到已有一个实例在运行")
        except Exception:
            pass
        # 能提到前台就直接提，省得用户看到一句"已经在运行"却找不到窗口
        if focus_existing():
            return
        notify("程序已经在运行，但没有找到它的窗口。\n\n"
               "可能是上一次退出没退干净，请在任务管理器里结束「超星AI答题」再重新打开。")
        return

    # 上次更新失败的说明读一次就删，弹窗比顶部提示条更不会漏看
    try:
        failure = pending_failure()
        if failure:
            notify("上次更新没有完成，当前还是旧版本。\n\n" + failure[:600])
    except Exception:
        pass

    if not port_in_use():
        threading.Thread(target=run_server, daemon=True).start()
        wait_ready()
        try:
            from webui.server import append_ui_log

            append_ui_log("界面已启动")
        except Exception:
            pass

    try:
        launch_window()
    except Exception as exc:  # WebView2 缺失等情况退回默认浏览器
        print(f"内嵌窗口启动失败（{exc}），改用默认浏览器打开。")
        _open_browser()
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    if "--run-task" in sys.argv:
        run_task()
    else:
        main()