"""pipeline_thread.py — Pipeline 独立 asyncio loop 线程管理。

职责单一：启动/停止 pipeline 线程，提供跨线程 send_action。
不知道 AppState、不知道 UI、不知道事件语义。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autosmartcut.pipeline.pipeline_session import PipelineSession

logger = logging.getLogger(__name__)


class PipelineThread:
    """管理 pipeline 的独立 asyncio loop 线程。

    生命周期：
    1. ``start()``       — 启动线程，等待 loop 就绪
    2. ``send_action()`` — 从外部线程安全地投递 action
    3. ``stop()``        — 设置 abort 标志并等待线程结束
    """

    def __init__(self, session: "PipelineSession") -> None:
        self._session = session
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = threading.Event()
        self._error: BaseException | None = None

    def start(self) -> None:
        """启动 pipeline 线程。非阻塞，等待 loop 就绪后返回。

        Raises:
            RuntimeError: pipeline 线程已在运行。
        """
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("pipeline 线程已在运行")
        self._error = None
        self._started.clear()
        self._thread = threading.Thread(
            target=self._entry, name="pipeline-loop", daemon=True,
        )
        self._thread.start()
        # 等待 loop 就绪（最多 5s），确保 send_action 可以安全调用
        self._started.wait(timeout=5.0)

    def _entry(self) -> None:
        """Pipeline 线程入口。创建独立 asyncio loop 并运行 session。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._started.set()
        try:
            loop.run_until_complete(self._session.start_async())
        except Exception as e:
            self._error = e
            logger.exception("Pipeline 线程异常: %s", e)
        finally:
            loop.close()
            self._loop = None

    def stop(self, timeout: float = 10.0) -> None:
        """设置 abort 标志并等待线程结束。

        Args:
            timeout: 等待线程结束的最大秒数。
        """
        self._session.abort(save=True)
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def send_action(self, action: object) -> None:
        """从外部线程安全地投递 action 到 pipeline 的 asyncio.Queue。

        通过 call_soon_threadsafe 保证线程安全。
        若 pipeline 线程尚未启动或已结束，调用静默忽略。
        """
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(
                self._session._action_queue.put_nowait, action
            )

    @property
    def is_alive(self) -> bool:
        """pipeline 线程是否正在运行。"""
        return self._thread is not None and self._thread.is_alive()

    @property
    def error(self) -> BaseException | None:
        """pipeline 线程的最终异常（正常结束时为 None）。"""
        return self._error
