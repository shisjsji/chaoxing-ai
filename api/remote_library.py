"""共享题库客户端。

只在整卷测验全对时上报，且上报内容不含账号——只用一个不可逆的用户标识哈希，
用于统计贡献和防刷。查询本身不需要身份。

网络不可达时静默降级，绝不阻塞答题。
"""

import configparser
import hashlib
import os
import time
from pathlib import Path

import requests

from api.logger import logger

CONFIG_PATH = Path("config.ini")
TIMEOUT = 2.5

# 内置后端，使用者无需配置
DEFAULT_ENDPOINT = "https://lingluandf.online/lib"
DEFAULT_TOKEN = "aQJgZoW93hzS93kXkUpo9SGpwbTbk-Nf"


def _conf() -> dict:
    """服务地址与令牌内置并固定启用，界面上不暴露任何配置项。

    这两个默认值是**公开的客户端令牌**（所有使用者共用，源码里可见），
    只用于访问本项目的共享题库。要指到你自己的服务，
    设置环境变量 CHAOXING_LIB_ENDPOINT / CHAOXING_LIB_TOKEN 即可。
    """
    endpoint = os.environ.get("CHAOXING_LIB_ENDPOINT", "").strip() or DEFAULT_ENDPOINT
    token = os.environ.get("CHAOXING_LIB_TOKEN", "").strip() or DEFAULT_TOKEN
    return {"enabled": True, "endpoint": endpoint, "token": token}


def enabled() -> bool:
    cfg = _conf()
    return bool(cfg.get("enabled") and cfg.get("endpoint") and cfg.get("token"))


def _request(method: str, url: str, **kwargs):
    """带重试的请求。域名刚解析生效时，本地 DNS 可能还缓存着「无此记录」。"""
    last_exc = None
    for attempt in range(3):
        try:
            return requests.request(method, url, **kwargs)
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(1.0 + attempt)
    raise last_exc


def friendly_error(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if "getaddrinfo failed" in lowered or "name does not resolve" in lowered \
            or "nameresolution" in lowered or "no address associated" in lowered:
        return "域名解析失败（DNS 缓存可能还停留在旧记录）。稍等几分钟重试，或把电脑 DNS 改成 223.5.5.5。"
    if "timed out" in lowered or "timeout" in lowered:
        return "连接超时，可能是网络链路不稳，请稍后重试。"
    if "ssl" in lowered or "certificate" in lowered:
        return "证书校验失败，请检查系统时间是否正确。"
    return text


def user_hash(username: str) -> str:
    """学习通账号 -> 不可逆标识，只用于防刷和统计贡献。"""
    if not username:
        return ""
    return hashlib.sha256(("chaoxing-ai:" + username).encode("utf-8")).hexdigest()[:32]


def lookup_batch(fingerprints: list[str]) -> dict:
    """批量查共享题库，返回 {指纹: 答案}。失败返回空 dict。"""
    cfg = _conf()
    if not (cfg.get("enabled") and cfg.get("endpoint") and cfg.get("token")):
        return {}
    fps = [f for f in fingerprints if f][:500]
    if not fps:
        return {}
    try:
        resp = _request(
            "POST",
            cfg["endpoint"] + "/api/v1/lookup",
            headers={"X-Token": cfg["token"]},
            json={"fingerprints": fps},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        answers = data.get("answers") or {}
        if answers:
            logger.info(f"共享题库命中 {len(answers)} 道题")
        return answers
    except Exception as exc:
        logger.debug(f"共享题库查询跳过: {exc}")
        return {}


def list_remote(limit: int = 50, keyword: str = "") -> dict:
    """拉取服务器题库列表，用于界面展示。"""
    cfg = _conf()
    if not (cfg.get("enabled") and cfg.get("endpoint") and cfg.get("token")):
        return {"ok": False, "error": "未启用共享题库"}
    try:
        resp = _request(
            "GET",
            cfg["endpoint"] + "/api/v1/list",
            headers={"X-Token": cfg["token"]},
            params={"limit": limit, "q": keyword},
            timeout=TIMEOUT + 1,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def submit_items(items: list[dict], username: str, confidence: str = "high") -> int:
    """上报题目，返回服务端接受的条数。

    confidence: high = 整卷全对验证过，medium = 部分正确、待多人交叉验证。
    """
    cfg = _conf()
    if not (cfg.get("enabled") and cfg.get("endpoint") and cfg.get("token")):
        return 0
    if not items:
        return 0
    uh = user_hash(username)
    if not uh:
        return 0
    try:
        payload_items = [dict(it, confidence=confidence) for it in items[:200]]
        resp = _request(
            "POST",
            cfg["endpoint"] + "/api/v1/submit",
            headers={"X-Token": cfg["token"]},
            json={"user": uh, "items": payload_items, "ts": int(time.time())},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        accepted = int(resp.json().get("accepted", 0))
        if accepted:
            logger.info(f"已向共享题库上报 {accepted} 道验证过的题")
        return accepted
    except Exception as exc:
        logger.debug(f"共享题库上报跳过: {exc}")
        return 0