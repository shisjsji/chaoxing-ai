"""Windows DPAPI 封装：加密只在本机、只对当前 Windows 账户可解。

命令行直接运行 main.py 时，配置里的密码/Key 是空的（它们存在加密文件里），
所以这里额外提供 read_saved_secrets() 供程序兜底读取。
"""

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

SECRET_FILE = "secret.dat"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _make_blob(data: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def available() -> bool:
    return os.name == "nt"


def protect(text: str) -> str:
    """明文 -> base64 密文。非 Windows 直接返回原文。"""
    if not text:
        return ""
    if not available():
        return text
    data = text.encode("utf-8")
    blob_in = _make_blob(data)
    blob_out = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise ctypes.WinError()
    try:
        raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return base64.b64encode(raw).decode("ascii")


def unprotect(token: str) -> str:
    """base64 密文 -> 明文。失败返回空串（当作没保存过）。"""
    if not token:
        return ""
    if not available():
        return token
    try:
        raw = base64.b64decode(token)
        blob_in = _make_blob(raw)
        blob_out = _DataBlob()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            return ""
        try:
            plain = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return plain.decode("utf-8", errors="replace")
    except Exception:
        return ""


def read_saved_secrets(path: str = SECRET_FILE) -> dict:
    """读取本机保存的密码与 Key，读不到返回空 dict。"""
    file = Path(path)
    if not file.is_file():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        "password": unprotect(data.get("password", "")),
        "api_key": unprotect(data.get("api_key", "")),
    }