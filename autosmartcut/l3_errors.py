from __future__ import annotations


class L3Error(RuntimeError):
    """L3 基类异常。"""


class L3InputError(L3Error):
    """输入/清单数据不合法。"""


class L3CacheError(L3Error):
    """缓存或 sidecar 相关错误。"""


class L3EncodeError(L3Error):
    """编码/后端执行失败。"""

