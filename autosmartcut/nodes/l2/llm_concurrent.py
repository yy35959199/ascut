"""LLM 并发调度基础设施。

职责：
    将多个同步阻塞调用并发执行，提供自适应并发上限、线程池调度、
    启动 jitter、首任务同步与 TTFT（Time To First Token，首 token 延迟）信号。

不做：
    重试（由 ``call_structured`` 等调用方内部负责）；本层只观察成功/失败以调整并发。

知道：
    ``LLMAPIError.status_code``（仅用于在异常时调整 ``AdaptiveThrottle`` 并发度）。

不知道：
    schema、prompt、具体业务块与决策结构。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class AdaptiveThrottle:
    """自适应并发上限。

    遇到 429/503 时 ``max_concurrency`` 减半（最低 1），
    距上次限速超过 ``recovery_window_s`` 后每次成功 +1 直至恢复 ``initial``。
    线程安全。
    """

    initial: int
    recovery_window_s: float = 30.0

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._initial = max(1, int(self.initial))
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
                "[llm-concurrent] 触发限速，并发上限降至 %d",
                self._max_conc,
            )

    def on_success_window(self) -> None:
        with self._lock:
            if time.monotonic() - self._last_throttle_time < self.recovery_window_s:
                return
            if self._max_conc < self._initial:
                self._max_conc = min(self._initial, self._max_conc + 1)


@dataclass
class ConcurrentTask(Generic[T]):
    """并发任务描述。

    ``key``：业务标识，原样写入 ``ConcurrentResult.key``。
    ``payload``：传给 ``call_fn(payload)`` 的自定义数据。
    """

    key: str
    payload: T


@dataclass
class ConcurrentResult(Generic[T]):
    """并发任务结果。"""

    key: str
    value: Any | None = None
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _is_throttle_error(exc: BaseException) -> bool:
    """检查异常链中是否有 ``status_code`` 为 429 或 503 的 ``LLMAPIError``。"""
    from autosmartcut.nodes.l2.intelligence_llm import LLMAPIError

    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and len(seen) < 8:
        oid = id(cur)
        if oid in seen:
            break
        seen.add(oid)
        if isinstance(cur, LLMAPIError):
            sc = int(getattr(cur, "status_code", 0) or 0)
            return sc in (429, 503)
        cur = cur.__cause__ or cur.__context__
    return False


def _run_one_safe(
    task: ConcurrentTask[T],
    call_fn: Callable[[T], Any],
    throttle: AdaptiveThrottle | None,
) -> ConcurrentResult[T]:
    """执行单个任务，捕获异常包装为 ``ConcurrentResult``；不做重试。"""
    try:
        value = call_fn(task.payload)
        if throttle:
            throttle.on_success_window()
        return ConcurrentResult(key=task.key, value=value)
    except Exception as e:
        if throttle and _is_throttle_error(e):
            throttle.on_throttle()
        logger.error("[llm-concurrent] 任务失败 key=%s: %s", task.key, e)
        return ConcurrentResult(key=task.key, error=e)


def dispatch_concurrent(
    tasks: list[ConcurrentTask[T]],
    call_fn: Callable[[T], Any],
    *,
    throttle: AdaptiveThrottle | None = None,
    first_sync: bool = True,
    first_done_event: threading.Event | None = None,
    ttft_timeout: float = 10.0,
    max_jitter_ms: int = 300,
) -> list[ConcurrentResult[T]]:
    """并发执行多个任务，返回与 ``tasks`` 顺序一致的结果列表。

    ``call_fn`` 须为同步函数；内部重试由 ``call_fn`` 自行处理。
    """
    if not tasks:
        return []

    if len(tasks) == 1:
        return [_run_one_safe(tasks[0], call_fn, throttle)]

    if not first_sync:
        max_workers = min(len(tasks), throttle.max_concurrency if throttle else 8)
        max_workers = max(1, max_workers)

        def _run_indexed(i: int, t: ConcurrentTask[T]) -> tuple[int, ConcurrentResult[T]]:
            jitter = random.randint(0, max_jitter_ms) / 1000.0
            if jitter > 0:
                time.sleep(jitter)
            return i, _run_one_safe(t, call_fn, throttle)

        results_map: dict[int, ConcurrentResult[T]] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            future_to_i = {ex.submit(_run_indexed, i, t): i for i, t in enumerate(tasks)}
            for fut in as_completed(future_to_i):
                i, res = fut.result()
                results_map[i] = res
        return [results_map[i] for i in range(len(tasks))]

    first = tasks[0]
    rest = tasks[1:]
    first_res = _run_one_safe(first, call_fn, throttle)

    if first_done_event is not None:
        if not first_done_event.wait(timeout=ttft_timeout):
            logger.warning(
                "[llm-concurrent] 首任务 TTFT 超时 %.1fs，继续并发其余任务",
                ttft_timeout,
            )
    # first_done_event 为 None 时，首任务已在上面同步跑完，无需再等待

    max_workers = min(len(rest), throttle.max_concurrency if throttle else 8)
    max_workers = max(1, max_workers)

    def _run_rest(i: int, t: ConcurrentTask[T]) -> tuple[int, ConcurrentResult[T]]:
        jitter = random.randint(0, max_jitter_ms) / 1000.0
        if jitter > 0:
            time.sleep(jitter)
        return i, _run_one_safe(t, call_fn, throttle)

    results_map_rest: dict[int, ConcurrentResult[T]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_i = {ex.submit(_run_rest, i, t): i for i, t in enumerate(rest)}
        for fut in as_completed(future_to_i):
            i, res = fut.result()
            results_map_rest[i] = res

    ordered_rest = [results_map_rest[i] for i in range(len(rest))]
    return [first_res] + ordered_rest
