"""把内嵌浏览器窗口贴进主界面留出来的那块区域。

pywebview 的窗口本来是独立原生窗口。这里用 Win32 把它改成主窗口的子窗口
（WS_CHILD + SetParent），再按页面里那块区域的坐标实时摆位置，看起来就是
嵌在页面里。

坐标来自前端上报：getBoundingClientRect() 是 CSS 像素，乘 devicePixelRatio
换算成物理像素；父客户区原点正好是视口左上角，所以直接能用。

每个页面一块区域、用一个窗口实例（目前只有考场页用），互不干扰。
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
# 0.25 秒一次就够：这个循环每轮都要跨语言调 Win32，跑太密会和后台线程抢 GIL，
# 表现为「拉考试列表奇慢」；而人眼对 0.25s 的跟随完全看不出来。
_POLL = 0.25


def _hwnd_of(win) -> int:
    handle = win.native.Handle
    return int(handle.ToInt64() if hasattr(handle, "ToInt64") else handle)


def _make_child(hwnd: int, tries: int = 6) -> bool:
    """去掉标题栏/边框/弹出样式，改成子窗口，并让系统重算边框。

    一次不一定压得住：WinForms 会在窗口可见性变化时按 FormBorderStyle
    重新套一遍样式，把 WS_CHILD 抹掉。抹掉之后窗口就变成「有父窗口的
    弹出窗」，位置按屏幕坐标算——表现就是点一下界面它跳一下。
    所以这里设完之后要回读确认，不对就再来一次。
    """
    for _ in range(max(1, tries)):
        style = _GetWindowLong(wintypes.HWND(hwnd), GWL_STYLE)
        if (style & WS_CHILD) and not (style & _POPUP_BITS):
            return True
        _SetWindowLong(wintypes.HWND(hwnd), GWL_STYLE,
                       (style & ~_POPUP_BITS) | WS_CHILD)
        user32.SetWindowPos(wintypes.HWND(hwnd), None, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
        time.sleep(0.05)
    return _still_child(hwnd)


def _still_child(hwnd: int) -> bool:
    style = _GetWindowLong(wintypes.HWND(hwnd), GWL_STYLE)
    return bool(style & WS_CHILD) and not (style & _POPUP_BITS)


class Stage:
    """一块嵌入区域。一个实例对应一个原生窗口。"""

    def __init__(self, label: str = "窗口"):
        self.label = label
        self._lock = threading.Lock()
        self._hwnd = 0
        self._win = None
        self._rect = None
        self._active = False
        self._shown = False
        self._applied = None

    # ---------- 对外 ----------

    def attach(self, main_win, child_win) -> bool:
        """把子窗口挂到主窗口里。失败返回 False，调用方退回独立窗口。"""
        if self._hwnd:
            return True
        try:
            parent = _hwnd_of(main_win)
            child = _hwnd_of(child_win)
        except Exception:
            return False
        if not parent or not child or parent == child:
            return False
        try:
            # 先让它显示一次，并给 WinForms 足够时间把 HWND 状态建完。
            # 隐藏状态下改样式会被它自己的 CreateParams 覆盖回去，
            # 表现为「窗口挂上了但样式没变」——位置按屏幕算，点一下跳一下。
            try:
                child_win.show()
            except Exception:
                pass
            time.sleep(0.35)
            if not _make_child(child, tries=12):
                return False

            user32.SetParent(wintypes.HWND(child), wintypes.HWND(parent))
            time.sleep(0.2)

            # SetParent 之后 Form 可能重建句柄，也可能把样式套回去，重新取一遍再压
            try:
                child = _hwnd_of(child_win)
            except Exception:
                pass
            if not _make_child(child, tries=12):
                return False
            # 这里不要再 ShowWindow(SW_HIDE)：隐藏动作会让 WinForms 按
            # FormBorderStyle 重套一遍样式，刚设的 WS_CHILD 就没了。
            # 窗口本来就还没显示过，保持现状即可。
        except Exception:
            return False
        with self._lock:
            self._hwnd = child
            self._win = child_win
        threading.Thread(target=self._loop, daemon=True).start()
        return True

    def set_rect(self, box) -> None:
        """前端上报区域矩形。box 的键是 left/top/width/height/dpr，没有就清空。"""
        if not box:
            with self._lock:
                self._rect = None
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
        with self._lock:
            self._rect = (left, top, width, height)

    def set_active(self, flag: bool) -> bool:
        """内容准备好了才把它显出来。返回是否已成功嵌入。"""
        with self._lock:
            self._active = bool(flag)
        return bool(self._hwnd)

    def embedded(self) -> bool:
        return bool(self._hwnd)

    def is_active(self) -> bool:
        return bool(self._active)

    def window(self):
        return self._win

    def detach(self) -> None:
        """还原成独立窗口，免得 pywebview 关窗口时卡在子窗口上。"""
        with self._lock:
            hwnd = self._hwnd
            self._hwnd = 0
            self._active = False
            self._rect = None
            self._applied = None
            self._shown = False
        if not hwnd:
            return
        try:
            user32.ShowWindow(wintypes.HWND(hwnd), SW_HIDE)
            user32.SetParent(wintypes.HWND(hwnd), None)
        except Exception:
            pass

    # ---------- 内部 ----------

    def make_standalone(self, win) -> bool:
        """嵌入失败时的退路：把窗口变回能拖、能关的普通窗口。"""
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

    def _loop(self) -> None:
        """盯着 active 和矩形变化，只在真的变了才挪窗口。

        每帧都调 SetWindowPos 会让 WebView2 一直重排，画面反而渲染不出来。
        """
        while True:
            time.sleep(_POLL)
            with self._lock:
                hwnd = self._hwnd
                rect = self._rect
                active = self._active
            if not hwnd:
                return
            if not active or not rect or rect[2] < _MIN_SIDE or rect[3] < _MIN_SIDE:
                if self._shown:
                    try:
                        user32.ShowWindow(wintypes.HWND(hwnd), SW_HIDE)
                    except Exception:
                        return
                    self._shown = False
                    self._applied = None
                elif user32.IsWindowVisible(wintypes.HWND(hwnd)):
                    # attach() 为了让 WinForms 把句柄建好，先 show() 过一次。
                    # 那条路径不会把 _shown 置真，所以这里必须按真实可见性兜底：
                    # 否则没人接管它，未加载页面的 WebView2 就一直是一块黑，
                    # 盖在界面上，用户看到的就是页面下半截全黑。
                    try:
                        user32.ShowWindow(wintypes.HWND(hwnd), SW_HIDE)
                    except Exception:
                        return
                continue
            if self._shown and rect == self._applied and _still_child(hwnd):
                continue
            if not _still_child(hwnd):
                try:
                    _make_child(hwnd)
                except Exception:
                    return
            left, top, width, height = rect
            if not self._shown:
                # 显示之前先把位置摆好，否则第一帧会出现在客户区左上角闪一下。
                try:
                    user32.SetWindowPos(wintypes.HWND(hwnd), None, left, top,
                                        width, height, SWP_NOACTIVATE)
                except Exception:
                    pass
                # 显示必须走 pywebview 自己的 show：直接 ShowWindow 的话 WinForms
                # 不重排布局，WebView2 的合成层还挂在 reparent 之前的位置上，
                # 结果就是一整块白板。
                win = self._win
                if win is not None:
                    try:
                        win.show()
                    except Exception:
                        pass
                # show 会让 WinForms 重套样式（WS_CHILD 被抹掉就变成按屏幕坐标
                # 摆放的弹出窗，看着就是「点一下跳一下」），所以补一次。
                time.sleep(0.12)
                _make_child(hwnd, tries=12)
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
            self._shown = True
            self._applied = rect


_exam = Stage("考场浏览器")

_stages = {"exam": _exam}


def get(name: str = "exam") -> Stage:
    return _stages.get(str(name or "exam").strip().lower(), _exam)


def detach_all() -> None:
    for stage in _stages.values():
        try:
            stage.detach()
        except Exception:
            pass


# ---------- 兼容旧调用：默认操作考试那个 ----------
def attach(main_win, exam_win) -> bool:
    return _exam.attach(main_win, exam_win)


def set_rect(box) -> None:
    _exam.set_rect(box)


def set_active(flag: bool) -> bool:
    return _exam.set_active(flag)


def embedded() -> bool:
    return _exam.embedded()


def is_active() -> bool:
    return _exam.is_active()


def detach() -> None:
    _exam.detach()


def make_standalone(win) -> bool:
    return _exam.make_standalone(win)