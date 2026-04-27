from __future__ import annotations


class L3Error(RuntimeError):
    """L3 基类异常。"""


class L3InputError(L3Error):
    """输入/清单数据不合法。"""
