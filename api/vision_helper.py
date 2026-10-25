"""题目配图处理：把题干里的 <img> 转成可以交给视觉模型的 data URL。

超星题目解析（api/decode.py 的 _extract_title）会把题干中的图片保留成
`<img src="...">`，这里负责把它下载、压缩、编码，并且从纯文本里剥离干净。
"""

import base64
import io
import re

import requests

IMG_RE = re.compile(r'<img[^>]*src=["\']([^"\']+)["\'][^>]*>', re.I)
MAX_IMAGES = 2
MAX_EDGE = 1280

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://mooc1.chaoxing.com/",
}


def extract_image_urls(text: str, limit: int = MAX_IMAGES) -> list[str]:
    return IMG_RE.findall(text or "")[:limit]


def strip_images(text: str, placeholder: str = "[题目配图]") -> str:
    return IMG_RE.sub(placeholder, text or "")


def to_data_url(url: str, timeout: int = 20) -> str | None:
    try:
        from PIL import Image

        if url.startswith("//"):
            url = "https:" + url
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content))
        img.thumbnail((MAX_EDGE, MAX_EDGE))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def build_image_parts(text: str, limit: int = MAX_IMAGES) -> tuple[str, list[dict]]:
    """返回（剥离图片后的文本，OpenAI 多模态 content 片段列表）。"""
    parts: list[dict] = []
    for url in extract_image_urls(text, limit):
        data = to_data_url(url)
        if data:
            parts.append({"type": "image_url", "image_url": {"url": data}})
    return strip_images(text), parts