import json
import os
import sys
import threading

from loguru import logger
from tqdm import tqdm

# loguru 的输出目标（保持模块加载时的原始 stderr）
tqdm_stream = sys.stderr

# 由 Web 控制台启动子进程时设置，用于给日志打章节标记并输出结构化进度事件
WEBUI_MODE = os.environ.get("CHAOXING_WEBUI") == "1"
# 标记内部分隔符，课程名与章节名分开传递（普通文本不会出现该控制字符）
SEP = "\x1f"

_chapter_ctx = threading.local()

# 日志缓冲区，用于在手动答题时缓存后台日志，答题结束后统一输出
log_buffer = []
MAX_LOG_BUFFER_SIZE = 1000


def set_chapter(name: str, course: str = "") -> None:
    """标记当前线程正在处理的章节，日志会带上该标记。"""
    _chapter_ctx.name = name or ""
    _chapter_ctx.course = course or ""


def get_chapter() -> str:
    return getattr(_chapter_ctx, "name", "")


def get_chapter_course() -> str:
    return getattr(_chapter_ctx, "course", "")


def emit_event(payload: dict) -> None:
    """向 Web 控制台发送一条结构化事件（章节进度等）。"""
    if not WEBUI_MODE:
        return
    try:
        print("@@EVENT@@" + json.dumps(payload, ensure_ascii=False), flush=True)
    except Exception:
        pass


def _patch_record(record) -> None:
    if not WEBUI_MODE:
        return
    chapter = get_chapter()
    if chapter:
        record["message"] = "@@CH|%s%s%s@@%s" % (get_chapter_course(), SEP, chapter, record["message"])


class _MarkedStderr:
    """给 tqdm 进度条输出补上章节标记，控制台才能把它归到正确的章节。

    进度条不走 loguru，而是直接写 stderr，所以在这里挂钩子。
    """

    def __init__(self, target):
        self._target = target

    def write(self, text):
        if not text:
            return 0
        chapter = get_chapter()
        if chapter and "%|" in text:
            text = "@@CH|%s%s%s@@%s" % (get_chapter_course(), SEP, chapter, text)
        return self._target.write(text)

    def flush(self):
        self._target.flush()

    def isatty(self):
        return False

    def __getattr__(self, item):
        return getattr(self._target, item)


def tqdm_sink(msg):
    manual_locked = False
    try:
        # 动态获取 api.answer 模块中的 TikuManual 锁，避免循环导入
        if 'api.answer' in sys.modules:
            TikuManual = getattr(sys.modules['api.answer'], 'TikuManual', None)
            if TikuManual and getattr(TikuManual, '_manual_lock', None):
                manual_locked = TikuManual._manual_lock.locked()
    except (AttributeError, KeyError, ImportError):
        pass

    if manual_locked:
        if len(log_buffer) < MAX_LOG_BUFFER_SIZE:
            log_buffer.append(msg)
    else:
        if log_buffer:
            for buffered_msg in log_buffer:
                tqdm.write(buffered_msg.rstrip(), file=tqdm_stream)
            log_buffer.clear()
        tqdm.write(msg.rstrip(), file=tqdm_stream)
    tqdm_stream.flush()


logger.remove()
if WEBUI_MODE:
    logger.configure(patcher=_patch_record)
    try:
        sys.stderr = _MarkedStderr(sys.stderr)
    except Exception:
        pass
logger.add(tqdm_sink, colorize=True, enqueue=True)
logger.add("chaoxing.log", rotation="10 MB", level="TRACE")