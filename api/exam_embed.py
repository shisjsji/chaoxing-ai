"""把考场窗口贴进主界面留出来的那块区域。

pywebview 的第二个窗口本来是个独立原生窗口。这里用 Win32 把它改成主窗口的
子窗口（WS_CHILD + SetParent），再按页面里那块区域的坐标实时摆位置，看起来
就是嵌在页面里。

坐标来自前端上报：getBoundingClientRect() 是 CSS 像素，乘 devicePixelRatio
换算成物理像素；父客户区原点正好是视口左上角，所以直接能用。
"""

import ctypes
import threading
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

_GetWindowLong = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
_SetWindowLong = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)

_GetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int]
_GetWindowLong.restype = ctypes.c_longlong
_SetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
_SetWindowLong.restype = ctypes.c_longlong
user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
user32.SetParent.restype = wintypes.HWND
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_uint]
user32.SetWindowPos.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL

GWL_STYLE = -16
WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
# WinForms 在显示窗口时会按 FormBorderStyle 重新套一遍样式，
# 所以每次摆放前都要把「看起来像独立窗口」的那几位置再清一次。
_POPUP_BITS = (WS_POPUP | WS_CAPTION | WS_SYSMENU | WS_THICKFRAME
               | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
SW_HIDE = 0
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040

# 太小的区域放网页没意义（页面切走、滚动出视口时会缩到 0）
_MIN_SIDE = 60
_POLL = 0.1

_lock = threading.Lock()
_state = {
    "hwnd": 0,
    "parent": 0,
    "win": None,
    "rect": None,
    "active": False,
    "shown": False,
    "applied": None,
    "failed": False,
}


def _hwnd_of(win) -> int:
    handle = win.native.Handle
    return int(handle.ToInt64() if hasattr(handle, "ToInt64") else handle)


def _make_child(hwnd: int) -> None:
    """去掉标题栏/边框/弹出样式，改成子窗口，并让系统重算边框。"""
    style = _GetWindowLong(wintypes.HWND(hwnd), GWL_STYLE)
    _SetWindowLong(wintypes.HWND(hwnd), GWL_STYLE,
                   (style & ~_POPUP_BITS) | WS_CHILD)
    user32.SetWindowPos(wintypes.HWND(hwnd), None, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)


def _still_child(hwnd: int) -> bool:
    style = _GetWindowLong(wintypes.HWND(hwnd), GWL_STYLE)
    return bool(style & WS_CHILD) and not (style & _POPUP_BITS)


def attach(main_win, exam_win) -> bool:
    """把考场窗口挂到主窗口里。失败返回 False，调用方退回独立窗口。"""
    if _state["hwnd"]:
        return True
    try:
        parent = _hwnd_of(main_win)
        child = _hwnd_of(exam_win)
    except Exception:
        _state["failed"] = True
        return False
    if not parent or not child or parent == child:
        _state["failed"] = True
        return False
    try:
        _make_child(child)
        user32.SetParent(wintypes.HWND(child), wintypes.HWND(parent))
        user32.ShowWindow(wintypes.HWND(child), SW_HIDE)
    except Exception:
        _state["failed"] = True
        return False
    with _lock:
        _state["hwnd"] = child
        _state["parent"] = parent
        _state["win"] = exam_win
    threading.Thread(target=_loop, daemon=True).start()
    return True


def set_rect(box) -> None:
    """前端上报区域矩形。box 的键是 left/top/width/height/dpr，没有就清空。"""
    if not box:
        with _lock:
            _state["rect"] = None
        return
    try:
        dpr = float(box.get("dpr") or 1)
        if not (0 < dpr <= 8):
            dpr = 1.0
        left = int(round(float(box.get("left") or 0) * dpr))
        top = int(round(float(box.get("top") or 0) * dpr))
        width = int(round(float(box.get("width") or 0) * dpr))
        height = int(round(float(box.get("height") or 0) * dpr))
    except (TypeError, ValueError, AttributeError):
        return
    with _lock:
        _state["rect"] = (left, top, width, height)


def set_active(flag: bool) -> bool:
    """考场窗口内容准备好了才把它显出来。返回是否已成功嵌入。"""
    with _lock:
        _state["active"] = bool(flag)
    return bool(_state["hwnd"])


def embedded() -> bool:
    return bool(_state["hwnd"])


def is_active() -> bool:
    return bool(_state["active"])


def make_standalone(win) -> bool:
    """嵌入失败时的退路：把考场窗口变回能拖、能关的普通窗口。

    它创建时是无边框的，嵌不进去又没标题栏就等于关不掉了。
    """
    try:
        hwnd = _hwnd_of(win)
        style = _GetWindowLong(wintypes.HWND(hwnd), GWL_STYLE)
        _SetWindowLong(
            wintypes.HWND(hwnd), GWL_STYLE,
            (style & ~WS_CHILD) | WS_POPUP | WS_CAPTION | WS_SYSMENU | WS_THICKFRAME)
        user32.SetWindowPos(wintypes.HWND(hwnd), None, 120, 90, 1080, 820,
                            SWP_NOZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW)
        return True
    except Exception:
        return False


def detach() -> None:
    """退出前还原成独立窗口，免得 pywebview 关窗口时卡在子窗口上。"""
    with _lock:
        hwnd = _state["hwnd"]
        _state["hwnd"] = 0
        _state["active"] = False
        _state["rect"] = None
        _state["applied"] = None
        _state["shown"] = False
    if not hwnd:
        return
    try:
        user32.ShowWindow(wintypes.HWND(hwnd), SW_HIDE)
        user32.SetParent(wintypes.HWND(hwnd), None)
    except Exception:
        pass


def _loop() -> None:
    """盯着 active 和矩形变化，只在真的变了才挪窗口。

    每帧都调 SetWindowPos 会让 WebView2 一直重排，画面反而渲染不出来。
    """
    while True:
        time.sleep(_POLL)
        with _lock:
            hwnd = _state["hwnd"]
            rect = _state["rect"]
            active = _state["active"]
        if not hwnd:
            return
        if not active or not rect or rect[2] < _MIN_SIDE or rect[3] < _MIN_SIDE:
            if _state["shown"]:
                try:
                    user32.ShowWindow(wintypes.HWND(hwnd), SW_HIDE)
                except Exception:
                    return
                _state["shown"] = False
                _state["applied"] = None
            continue
        if _state["shown"] and rect == _state["applied"] and _still_child(hwnd):
            continue
        if not _still_child(hwnd):
            try:
                _make_child(hwnd)
            except Exception:
                return
        left, top, width, height = rect
        if not _state["shown"]:
            # 显示之前先把位置摆好，否则第一帧会出现在客户区左上角闪一下。
            try:
                user32.SetWindowPos(wintypes.HWND(hwnd), None, left, top, width, height,
                                    SWP_NOACTIVATE)
            except Exception:
                pass
            # 显示必须走 pywebview 自己的 show：直接 ShowWindow 的话 WinForms
            # 不重排布局，WebView2 的合成层还挂在 reparent 之前的位置上，
            # 结果就是一整块白板。
            win = _state.get("win")
            if win is not None:
                try:
                    win.show()
                except Exception:
                    pass
        try:
            # 带到 HWND_TOP：子窗口之间也有层级，不置顶会被主界面的
            # WebView2 控件盖在下面。
            ok = user32.SetWindowPos(
                wintypes.HWND(hwnd), None, left, top, width, height,
                SWP_NOACTIVATE | SWP_SHOWWINDOW)
        except Exception:
            return
        if not ok:
            continue
        _state["shown"] = True
        _state["applied"] = rect