"""2b 分块并发调度：分块流式收集与结果聚合。

线程池、首块 TTFT（首 token 延迟）与自适应并发委托
``llm_concurrent.dispatch_concurrent``；单次 LLM 调用与重试由
``intelligence_llm.call_structured`` 负责。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from autosmartcut.nodes.l2.intelligence_llm import StreamChunk, call_structured
from autosmartcut.nodes.l2.llm_concurrent import (
    AdaptiveThrottle,
    ConcurrentTask,
    dispatch_concurrent,
)

if TYPE_CHECKING:
    from autosmartcut.nodes.l2.intelligence_llm import StructuredResult

logger = logging.getLogger(__name__)


@dataclass
class BlockTask:
    """单块 LLM 任务描述。"""

    block_ordinal: int  # 1-based
    n_blocks: int
    stage: str  # "decision_r1" / "decision_r2"
    messages: list[dict]
    schema: dict
    allowed_indices: set[int]
    input_summary: str


@dataclass
class BlockResult:
    """单块结构化结果。"""

    block_ordinal: int
    structured_result: "StructuredResult"
    decisions: list[dict]


@dataclass
class PreliminaryDecision:
    """R1 输出的单条决策。"""

    index: int
    keep: bool
    reason: str


class BlockStreamCollector:
    """线程安全收集各块 StreamChunk，可选订阅者。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buffers: dict[int, list[StreamChunk]] = {}
        self._status: dict[int, str] = {}
        self._listeners: list[Callable[[int, StreamChunk], None]] = []

    def subscribe(self, listener: Callable[[int, StreamChunk], None]) -> None:
        self._listeners.append(listener)

    def make_on_chunk(self, block_ordinal: int) -> Callable[[StreamChunk], None]:
        def _on_chunk(chunk: StreamChunk) -> None:
            with self._lock:
                self._buffers.setdefault(block_ordinal, []).append(chunk)
                st = self._status.get(block_ordinal, "pending")
                if chunk.event == "retry":
                    pass
                elif chunk.event == "result":
                    self._status[block_ordinal] = "done"
                elif chunk.event in ("reasoning_delta", "content_delta"):
                    if st == "pending":
                        self._status[block_ordinal] = "streaming"
                elif chunk.event == "usage":
                    pass
                listeners = list(self._listeners)
            for fn in listeners:
                try:
                    fn(block_ordinal, chunk)
                except Exception as e:
                    logger.warning("[BlockStreamCollector] listener 异常: %s", e)

        return _on_chunk

    def get_buffer(self, block_ordinal: int) -> list[StreamChunk]:
        with self._lock:
            return list(self._buffers.get(block_ordinal, []))

    def get_all_status(self) -> dict[int, str]:
        with self._lock:
            return dict(self._status)

    def register_block(self, block_ordinal: int) -> None:
        with self._lock:
            self._buffers.setdefault(block_ordinal, [])
            self._status.setdefault(block_ordinal, "pending")


def _wrap_ttft_on_chunk(
    orig: Callable[[StreamChunk], None] | None,
    evt: threading.Event,
) -> Callable[[StreamChunk], None]:
    """首块 on_chunk：在首个 delta 到达时 set ``evt``，再转发原回调。"""

    def _wrapped(chunk: StreamChunk) -> None:
        if not evt.is_set():
            if chunk.event in ("reasoning_delta", "content_delta") and (
                chunk.reasoning_delta or chunk.content_delta
            ):
                evt.set()
        if orig is not None:
            orig(chunk)

    return _wrapped


def dispatch_blocks_parallel(
    tasks: list[BlockTask],
    sanitize_fn: Callable[["BlockTask", "StructuredResult"], BlockResult],
    throttle: AdaptiveThrottle | None = None,
    collector: BlockStreamCollector | None = None,
    *,
    ttft_timeout: float = 10.0,
    max_jitter_ms: int = 300,
) -> list[BlockResult]:
    """2b 分块并发：委托通用调度器，再用 ``sanitize_fn`` 转为 ``BlockResult``。"""
    if not tasks:
        return []

    ordered = sorted(tasks, key=lambda x: x.block_ordinal)
    ttft_event = threading.Event()

    concurrent_tasks: list[
        ConcurrentTask[tuple[BlockTask, Callable[[StreamChunk], None] | None]]
    ] = []
    for i, task in enumerate(ordered):
        oc = collector.make_on_chunk(task.block_ordinal) if collector else None
        if i == 0 and len(ordered) > 1:
            oc = _wrap_ttft_on_chunk(oc, ttft_event)
        concurrent_tasks.append(
            ConcurrentTask(key=str(task.block_ordinal), payload=(task, oc))
        )

    def _call_llm(
        payload: tuple[BlockTask, Callable[[StreamChunk], None] | None],
    ) -> StructuredResult:
        bt, on_chunk = payload
        return call_structured(
            bt.messages,
            bt.schema,
            bt.stage,
            on_chunk=on_chunk,
        )

    results = dispatch_concurrent(
        concurrent_tasks,
        _call_llm,
        throttle=throttle,
        first_sync=True,
        first_done_event=ttft_event if len(ordered) > 1 else None,
        ttft_timeout=ttft_timeout,
        max_jitter_ms=max_jitter_ms,
    )

    block_results: list[BlockResult] = []
    for cr, task in zip(results, ordered, strict=True):
        if cr.ok and cr.value is not None:
            block_results.append(sanitize_fn(task, cr.value))
        elif cr.error is not None:
            raise cr.error
        else:
            raise RuntimeError(
                f"[2b-dispatch] 无结果且无异常 block={task.block_ordinal}"
            )
    return block_results
