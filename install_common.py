"""安装器与卸载器共用的常量和小工具。

两边都要知道「哪些是程序文件、哪些是用户资料」，一旦名字对不上，
卸载就会要么删不干净、要么误删用户的题库和账号，所以集中放这里。
"""

import os
import subprocess
from pathlib import Path

APP_NAME = "超星AI答题"
APP_DISPLAY = "超星 AI 答题"
EXE_NAME = f"{APP_NAME}.exe"
UNINSTALLER_NAME = "卸载.exe"
LEGACY_UNINSTALLER = "uninstall.exe"

DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / APP_NAME
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_NAME}"

# 程序自身的文件：卸载时一律删除（覆盖安装时先清掉再解压）
PROGRAM_ITEMS = {"_internal", EXE_NAME, UNINSTALLER_NAME, LEGACY_UNINSTALLER}

# 用户可以带走的东西：保留资料时原地留下
DATA_ITEMS = [
    ("config.ini", "配置（模型、课程、并发等）"),
    ("secret.dat", "加密保存的密码与 API Key"),
    ("cookies.txt", "学习通登录状态"),
    ("library.db", "本地题库"),
    ("cache.json", "课程与考试缓存"),
    (".device-id", "设备标识"),
    ("chaoxing.log", "运行日志"),
]


def webview_profile_dir() -> Path:
    """程序内 WebView2 的配置目录，考场登录状态就存在这里。"""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / APP_NAME / "webview"


def desktop_dir() -> Path:
    return Path(os.path.join(os.environ["USERPROFILE"], "Desktop"))


def start_menu_dir() -> Path:
    return Path(os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                             "Start Menu", "Programs"))


def shortcut_paths() -> list:
    return [desktop_dir() / f"{APP_DISPLAY}.lnk", start_menu_dir() / f"{APP_DISPLAY}.lnk"]


def remove_shortcuts() -> None:
    for link in shortcut_paths():
        try:
            if link.exists():
                link.unlink()
        except Exception:
            pass


def delete_registry() -> None:
    try:
        import winreg

        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except Exception:
        pass


def read_installed() -> dict:
    """读注册表里的已安装信息，没有就返回空。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            def get(name):
                try:
                    return winreg.QueryValueEx(key, name)[0]
                except Exception:
                    return ""

            return {
                "version": str(get("DisplayVersion") or ""),
                "location": str(get("InstallLocation") or ""),
                "display_name": str(get("DisplayName") or ""),
            }
    except Exception:
        return {}


def dir_size(path: Path) -> int:
    total = 0
    try:
        if path.is_file():
            return path.stat().st_size
        for item in path.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except Exception:
                pass
    except Exception:
        pass
    return total


def taskkill_app() -> None:
    subprocess.run(["taskkill", "/F", "/IM", EXE_NAME], capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def human_size(num: int) -> str:
    if num >= 1024 ** 3:
        return f"{num / 1024 ** 3:.1f} GB"
    if num >= 1024 ** 2:
        return f"{num / 1024 ** 2:.0f} MB"
    if num >= 1024:
        return f"{num / 1024:.0f} KB"
    return f"{num} B"