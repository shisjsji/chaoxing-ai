"""把日志、报错和使用反馈上报到服务器。

设计取舍：
- 只报版本、模块、错误摘要和少量上下文，**不自动上报题目原文、账号、cookie**。
- 用户主动点「反馈」时可以附带最近日志，界面上会说明会发什么。
- 网络不通就静默放弃，绝不阻塞主流程。
"""

import os
import uuid
from pathlib import Path

import requests

from api.logger import logger

DEVICE_FILE = Path(".device-id")
TIMEOUT = 6


def device_id() -> str:
    """本机一个稳定匿名标识，用于限额与去重。不含任何账号信息。"""
    try:
        if DEVICE_FILE.exists():
            value = DEVICE_FILE.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = uuid.uuid4().hex[:16]
        DEVICE_FILE.write_text(value, encoding="utf-8")
        return value
    except OSError:
        return "unknown"


def push(kind: str, module: str, message: str, context: str = "") -> bool:
    """上报一条记录。返回是否送达，失败不抛异常。"""
    from api.remote_library import _conf
    from api.updater import APP_VERSION

    cfg = _conf()
    endpoint = (cfg.get("endpoint") or "").rstrip("/")
    token = cfg.get("token") or ""
    if not endpoint or not token:
        return False
    if not (message or context):
        return False

    payload = {
        "kind": (kind or "log")[:20],
        "version": APP_VERSION,
        "module": (module or "ui")[:40],
        "message": (message or "")[:4000],
        "context": (context or "")[:4000],
        "device": device_id(),
    }
    try:
        resp = requests.post(endpoint + "/api/v1/feedback",
                             headers={"X-Token": token},
                             json=payload, timeout=TIMEOUT)
        return resp.status_code == 200 and resp.json().get("ok") is True
    except Exception as exc:
        logger.debug(f"反馈上报跳过: {exc}")
        return False


def push_error(module: str, exc: BaseException, context: str = "") -> bool:
    """上报一个异常。只带类型和消息，不带堆栈里可能的本地路径细节。"""
    text = f"{type(exc).__name__}: {exc}"
    return push("error", module, text, context)