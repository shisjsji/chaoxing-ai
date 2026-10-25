"""启动本地控制台。

开发环境：.venv\\Scripts\\python.exe run_webui.py
打包后：双击 chaoxing-ai.exe

带 --run-task 参数时进入任务子进程模式（供控制台内部调用，跑刷课主流程）。
"""

import os
import socket
import sys
import threading
import webbrowser

HOST, PORT = "127.0.0.1", 5000
URL = f"http://{HOST}:{PORT}"


def base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.6)
        return sock.connect_ex((host, port)) == 0


def open_browser() -> None:
    try:
        webbrowser.open(URL)
    except Exception:
        pass


def run_task() -> None:
    sys.argv = [a for a in sys.argv if a != "--run-task"]
    os.chdir(base_dir())
    # 打包后子进程的 stdio 默认跟随系统编码，这里强制 UTF-8，避免父进程解码成乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    import main as chaoxing_main

    chaoxing_main.main()


def run_webui() -> None:
    os.chdir(base_dir())

    # 已经有一个控制台在跑（重复双击）时，直接把浏览器叫起来就好
    if port_in_use(HOST, PORT):
        print(f"控制台已在运行: {URL}")
        print("正在打开浏览器，无需重复启动。")
        open_browser()
        return

    from webui.server import app

    threading.Timer(1.2, open_browser).start()
    print(f"控制台地址: {URL}")
    print("浏览器会自动打开；如果没有反应，请手动在浏览器里访问上面的地址。")
    print("关闭这个窗口即退出程序。")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    if "--run-task" in sys.argv:
        run_task()
    else:
        run_webui()