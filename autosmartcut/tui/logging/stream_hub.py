"""实时日志行发布订阅（与 Textual 无关）。"""

from __future__ import annotations

from collections.abc import Callable


class LogStreamHub:
    """单向广播：publish 后同步通知所有订阅者。

    线程安全契约：
    - publish() 必须在 UI 线程调用（由 call_later 保证）
    - 订阅者回调必须快速返回（O(1)），不得做 I/O 或重计算
    - 需要异步处理的订阅者应在回调里投递到自己的队列，不得在回调里阻塞
    """

    def __init__(self) -> None:
        self._subs: dict[int, Callable[[str], None]] = {}
        self._next_id: int = 0

    def publish(self, line: str) -> None:
        """广播一行文本（通常已格式化，无尾换行）。"""
        if not line:
            return
        for cb in list(self._subs.values()):
            try:
                cb(line)
            except Exception:
                pass

    def subscribe(self, consumer: Callable[[str], None]) -> int:
        """注册消费者，返回 token；``unsubscribe`` 时使用。"""
        self._next_id += 1
        tid = self._next_id
        self._subs[tid] = consumer
        return tid

    def unsubscribe(self, token: int) -> None:
        self._subs.pop(token, None)
