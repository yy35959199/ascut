"""2b 分块并发调度：线程池 + TTFT 首块建缓存 + jitter + 429/503 退避。

配合同步阻塞的 ``call_structured``（SSE 流式），不在 LLM 层引入 asyncio。
"""
from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from autosmartcut.nodes.l2.intelligence_llm import StreamChunk

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


class AdaptiveThrottle:
    """自适应并发上限（遇到 429/503 时收缩，成功后逐步恢复）。"""

    def __init__(self, initial: int) -> None:
        self._lock = threading.Lock()
        self._initial = max(1, initial)
        self._max_conc = self._initial
        self._last_throttle_time = 0.0

    @property
    def max_concurrency(self) -> int:
        with self._lock:
            return self._max_conc

    def on_throttle(self) -> None:
        with self._lock:
            self._max_conc = max(1, self._max_conc // 2)
            self._last_throttle_time = time.monotonic()
            logger.warning(
                "[2b-dispatch] 触发限速，并发上限降至 %d",
                self._max_conc,
            )

    def on_success_window(self) -> None:
        """若距上次限速超过 30s，尝试 +1 并发直至 initial。"""
        with self._lock:
            if time.monotonic() - self._last_throttle_time < 30.0:
                return
            if self._max_conc < self._initial:
                self._max_conc = min(self._initial, self._max_conc + 1)


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


def _is_delta_chunk(chunk: StreamChunk) -> bool:
    return chunk.event in ("reasoning_delta", "content_delta") and (
        bool(chunk.reasoning_delta) or bool(chunk.content_delta)
    )


def _call_one_block_with_retry(
    task: BlockTask,
    call_fn: Callable[
        [BlockTask, Callable[[StreamChunk], None] | None],
        BlockResult,
    ],
    throttle: AdaptiveThrottle | None,
    on_chunk: Callable[[StreamChunk], None] | None,
    *,
    max_retries: int = 5,
) -> BlockResult:
    from autosmartcut.nodes.l2.intelligence_llm import LLMAPIError

    attempt = 0
    while attempt < max_retries:
        try:
            if throttle:
                throttle.on_success_window()
            return call_fn(task, on_chunk)
        except LLMAPIError as e:
            sc = int(getattr(e, "status_code", 0) or 0)
            if sc in (429, 503) and throttle:
                throttle.on_throttle()
            attempt += 1
            if sc in (429, 503) and attempt < max_retries:
                base = 1.0 * (2 ** (attempt - 1)) + random.uniform(0.0, 0.5)
                if sc == 503:
                    base = max(base, 5.0)
                delay = min(base, 30.0)
                logger.warning(
                    "[2b-dispatch] API %s，%.1fs 后重试 (%d/%d)",
                    sc,
                    delay,
                    attempt,
                    max_retries,
                )
                time.sleep(delay)
                continue
            raise
    raise RuntimeError("不应到达此处")


def dispatch_blocks_parallel(
    tasks: list[BlockTask],
    call_fn: Callable[
        [BlockTask, Callable[[StreamChunk], None] | None],
        BlockResult,
    ],
    throttle: AdaptiveThrottle | None = None,
    collector: BlockStreamCollector | None = None,
    *,
    ttft_timeout: float = 10.0,
    max_jitter_ms: int = 300,
) -> list[BlockResult]:
    """首块同步等待 TTFT，其余块线程池 + jitter 并发。"""
    if not tasks:
        return []

    ordered = sorted(tasks, key=lambda x: x.block_ordinal)
    if len(ordered) == 1:
        t0 = ordered[0]
        oc = collector.make_on_chunk(t0.block_ordinal) if collector else None
        return [_call_one_block_with_retry(t0, call_fn, throttle, oc)]

    first = ordered[0]
    rest = ordered[1:]

    ttft_event = threading.Event()
    col_first = (
        collector.make_on_chunk(first.block_ordinal) if collector else None
    )

    def _on_first(chunk: StreamChunk) -> None:
        if not ttft_event.is_set() and _is_delta_chunk(chunk):
            ttft_event.set()
        if col_first is not None:
            col_first(chunk)

    r_first = _call_one_block_with_retry(
        first,
        call_fn,
        throttle,
        _on_first,
    )

    if not ttft_event.wait(timeout=ttft_timeout):
        logger.warning(
            "[2b-dispatch] 首块 TTFT 超时 %.1fs，继续并发剩余块",
            ttft_timeout,
        )

    max_workers = min(
        len(rest),
        throttle.max_concurrency if throttle else 8,
    )
    max_workers = max(1, max_workers)

    def _run_one(i: int, task: BlockTask) -> tuple[int, BlockResult]:
        jitter = random.randint(0, max_jitter_ms) / 1000.0
        if jitter > 0:
            time.sleep(jitter)
        oc = collector.make_on_chunk(task.block_ordinal) if collector else None
        br = _call_one_block_with_retry(task, call_fn, throttle, oc)
        return i, br

    results_map: dict[int, BlockResult] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_i = {
            ex.submit(_run_one, i, task): i
            for i, task in enumerate(rest)
        }
        for fut in as_completed(future_to_i):
            i, br = fut.result()
            results_map[i] = br

    ordered_rest = [results_map[i] for i in range(len(rest))]
    return [r_first] + ordered_rest
