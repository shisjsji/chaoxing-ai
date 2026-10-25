"""桌面应用入口：内嵌 WebView 窗口 + 系统托盘。

关闭窗口不会退出程序，而是收进托盘继续在后台运行；从托盘可以重新打开或彻底退出。
开发环境：.venv\\Scripts\\python.exe desktop.py
带 --run-task 时进入任务子进程模式。
"""

import os
import socket
import subprocess
import sys
import threading
import time

HOST, PORT = "127.0.0.1", 5000
URL = f"http://{HOST}:{PORT}"


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


def launch_window() -> None:
    import webview

    window = webview.create_window(
        "超星 AI 答题",
        URL,
        width=1180,
        height=840,
        min_size=(980, 680),
        background_color="#f2f2f7",
        text_select=True,
    )

    # 考试专用窗口：进考场要过图形验证码和客户端签名，交给真浏览器最稳，
    # 程序只从它的地址栏里取 enc 然后自动答题。
    exam = webview.create_window(
        "考试（在此进入考场）",
        "about:blank",
        width=1080,
        height=820,
        hidden=True,
        text_select=True,
    )
    from webui.server import set_exam_window

    set_exam_window(exam)

    state = {"tray": None, "quitting": False}

    def quit_app(*_args) -> None:
        state["quitting"] = True
        try:
            if state["tray"] is not None:
                state["tray"].stop()
        except Exception:
            pass
        try:
            window.destroy()
        except Exception:
            pass

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
    webview.start()


def _open_browser() -> None:
    import webbrowser

    webbrowser.open(URL)


def main() -> None:
    os.chdir(base_dir())

    if not port_in_use():
        threading.Thread(target=run_server, daemon=True).start()
        wait_ready()

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