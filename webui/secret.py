"""凭据加密的薄封装，实现见 api/dpapi.py（命令行模式也要用）。"""

from api.dpapi import available, protect, unprotect

__all__ = ["available", "protect", "unprotect"]
