"""联网搜索。

只认一种通用协议：POST <url>，body 里带 query，响应里尽力把结果捞出来。
博查、Tavily 这类搜索 API 的返回结构不一样，这里从上到下试着兼容几种常见形状，
不用让使用者去选「你用的是哪家」。

没配置、超时、返回看不懂，一律返回空列表 —— 搜索是锦上添花，
任何情况下都不该拖住答题或审查流程。
"""

import json
from configparser import ConfigParser
from pathlib import Path

import requests

from api.dpapi import unprotect
from api.logger import logger

CONFIG_PATH = Path("config.ini")
SECRET_PATH = Path("secret.dat")
TIMEOUT = 12
_MAX_SNIPPET = 800


def _conf() -> dict:
    url, key, enabled = "", "", False
    if CONFIG_PATH.exists():
        parser = ConfigParser(interpolation=None)
        try:
            parser.read(CONFIG_PATH, encoding="utf-8")
        except Exception:
            parser = ConfigParser(interpolation=None)
        section = parser["search"] if "search" in parser else {}
        url = str(section.get("url", "") or "").strip()
        enabled = str(section.get("enabled", "true")).strip().lower() != "false"
        key = str(section.get("key", "") or "").strip()

    if not key and SECRET_PATH.exists():
        try:
            data = json.loads(SECRET_PATH.read_text(encoding="utf-8"))
            key = unprotect(data.get("search_key", "") or "")
        except Exception:
            pass
    return {"enabled": enabled, "url": url, "key": key}


def configured() -> bool:
    conf = _conf()
    return bool(conf["enabled"] and conf["url"] and conf["key"])


def _looks_like_tavily(url: str) -> bool:
    return "tavily" in url.lower()


def _pick(item: dict) -> dict:
    """把各家不同的字段名归一成 title/url/snippet。"""
    title = item.get("title") or item.get("name") or ""
    link = item.get("url") or item.get("link") or item.get("href") or ""
    body = (item.get("snippet") or item.get("content") or item.get("summary")
            or item.get("description") or item.get("text") or "")
    return {
        "title": str(title).strip(),
        "url": str(link).strip(),
        "snippet": str(body).strip()[:_MAX_SNIPPET],
    }


def _extract(data) -> list:
    """从各种可能的返回结构里把结果列表找出来。"""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]

    if not isinstance(data, dict):
        return []

    # Tavily: {results: [...]}
    for key in ("results", "items", "documents"):
        value = data.get(key)
        if isinstance(value, list) and value:
            return [x for x in value if isinstance(x, dict)]

    # 博查: webPages 在顶层，有些网关照 OpenAI 风格包在 data 里
    for holder in (data, data.get("data")):
        if not isinstance(holder, dict):
            continue
        for key in ("webPages", "web_pages", "pages"):
            pages = holder.get(key)
            if isinstance(pages, dict):
                value = pages.get("value") or pages.get("results")
                if isinstance(value, list) and value:
                    return [x for x in value if isinstance(x, dict)]
            if isinstance(pages, list) and pages:
                return [x for x in pages if isinstance(x, dict)]

    inner = data.get("data")
    if isinstance(inner, dict):
        found = _extract(inner)
        if found:
            return found

    # 有些网关照 OpenAI 的格式包了一层
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        try:
            content = choices[0]["message"]["content"]
            parsed = json.loads(content)
            return _extract(parsed)
        except Exception:
            pass

    return []


def search(query: str, count: int = 5, conf: dict | None = None) -> list:
    """搜索并按 title/url/snippet 返回。任何异常都吞掉。

    conf 不传就读磁盘上的配置；界面里「测试」时可以直接把刚填的值塞进来。
    """
    query = str(query or "").strip()
    if not query:
        return []
    conf = conf or _conf()
    if not (conf.get("enabled", True) and conf.get("url") and conf.get("key")):
        return []

    url = conf["url"]
    # 博查和 Tavily 现在都认 Authorization: Bearer，统一走这个
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {conf['key']}"}
    if _looks_like_tavily(url):
        body = {"query": query,
                "max_results": max(1, min(10, int(count or 5))),
                "search_depth": "basic"}
    else:
        body = {"query": query, "count": max(1, min(10, int(count or 5))),
                "summary": True}

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=TIMEOUT)
        resp.raise_for_status()
        results = _extract(resp.json())
    except Exception as exc:
        logger.debug(f"联网搜索跳过：{exc}")
        return []

    cleaned = []
    for item in results:
        picked = _pick(item)
        if picked["snippet"] or picked["title"]:
            cleaned.append(picked)
        if len(cleaned) >= count:
            break
    if cleaned:
        logger.info(f"联网搜索「{query[:20]}」命中 {len(cleaned)} 条")
    return cleaned


def render(results: list, limit: int = 5) -> str:
    """把搜索结果压成给模型看的短文本。"""
    if not results:
        return ""
    lines = []
    for i, item in enumerate(results[:limit], 1):
        title = item.get("title") or "（无标题）"
        lines.append(f"[{i}] {title}")
        if item.get("snippet"):
            lines.append(f"    {item['snippet']}")
        if item.get("url"):
            lines.append(f"    来源：{item['url']}")
    return "\n".join(lines)