"""LLM 并发调度基础设施。

职责：
    将多个同步阻塞调用并发执行，提供自适应并发上限、工作队列调度、
    启动 jitter、首任务同步与 TTFT（Time To First Token，首 token 延迟）信号。

调度模型：工作队列 + 动态并发度。
    - 失败的传输异常任务放回队列末尾，等其他连接释放后重试。
    - 每次传输异常触发 throttle.on_throttle()，减半并发度。
    - 每个任务最多重入队 max_task_retries 次。
    - 不可重试的错误（SCHEMA_ERROR、401 等）直接记录为最终失败。

不做：
    重试（由 ``call_structured`` 等调用方内部负责）；本层只观察成功/失败以调整并发。

知道：
    ``LLMAPIError.status_code``（仅用于在异常时调整 ``AdaptiveThrottle`` 并发度）。
    ``_is_retryable_transport_error``（判断是否为可重入队的传输异常）。

不知道：
    schema、prompt、具体业务块与决策结构。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class AdaptiveThrottle:
    """自适应并发上限。

    遇到 429/503 或传输异常时 ``max_concurrency`` 减半（最低 1），
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


def _is_retryable_transport(exc: Exception | None) -> bool:
    """判断异常是否为可重入队的传输层异常。

    复用 intelligence_llm 的判断逻辑，避免重复维护异常类型列表。
    注意：429/503（LLMAPIError）不在此列——它们由 call_structured 内部重试处理，
    不需要重入队。
    """
    if exc is None:
        return False
    from autosmartcut.nodes.l2.intelligence_llm import _is_retryable_transport_error
    return _is_retryable_transport_error(exc)


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


def _run_one_with_jitter(
    task: ConcurrentTask[T],
    call_fn: Callable[[T], Any],
    throttle: AdaptiveThrottle | None,
    jitter_s: float,
) -> ConcurrentResult[T]:
    """在线程内 sleep jitter 后执行任务。

    jitter 在线程内 sleep 而不是在主循环 sleep，
    避免主循环被阻塞（主循环需要持续监听 active futures）。
    """
    if jitter_s > 0:
        time.sleep(jitter_s)
    return _run_one_safe(task, call_fn, throttle)


def _run_queue(
    indexed_tasks: list[tuple[int, ConcurrentTask[T]]],
    initial_max_workers: int,
    call_fn: Callable[[T], Any],
    throttle: AdaptiveThrottle | None,
    max_jitter_ms: int,
    max_task_retries: int,
) -> dict[int, ConcurrentResult[T]]:
    """工作队列调度循环。

    Args:
        indexed_tasks: (原始 index, task) 列表，index 用于最终结果对齐。
        initial_max_workers: 初始线程池大小。
        call_fn: 同步调用函数。
        throttle: 自适应并发控制器。
        max_jitter_ms: 任务启动前的最大随机延迟（毫秒）。
        max_task_retries: 每个任务最多重入队次数。

    Returns:
        index → ConcurrentResult 的映射，覆盖所有输入 index。
    """
    pending: deque[tuple[int, ConcurrentTask[T]]] = deque(indexed_tasks)
    active: dict[Future, tuple[int, ConcurrentTask[T]]] = {}
    results: dict[int, ConcurrentResult[T]] = {}
    retry_counts: dict[str, int] = {}  # task.key → 已重入队次数

    executor = ThreadPoolExecutor(max_workers=initial_max_workers)
    try:
        while pending or active:
            # ── 填充：从 pending 取任务，直到 active 数量达到当前并发上限 ──
            current_limit = (
                throttle.max_concurrency if throttle else initial_max_workers
            )
            while pending and len(active) < current_limit:
                idx, task = pending.popleft()
                jitter_s = random.randint(0, max_jitter_ms) / 1000.0
                fut: Future = executor.submit(
                    _run_one_with_jitter, task, call_fn, throttle, jitter_s
                )
                active[fut] = (idx, task)

            if not active:
                # current_limit=0 的极端情况（理论上 on_throttle 保证最低 1）
                break

            # ── 等待任意一个完成 ──
            done, _ = wait(active.keys(), return_when=FIRST_COMPLETED)

            # ── 处理完成的任务 ──
            for fut in done:
                idx, task = active.pop(fut)
                cr: ConcurrentResult[T] = fut.result()

                if cr.ok:
                    results[idx] = cr

                elif _is_retryable_transport(cr.error):
                    retries = retry_counts.get(task.key, 0)
                    if retries < max_task_retries:
                        retry_counts[task.key] = retries + 1
                        pending.append((idx, task))  # 放回队列末尾
                        if throttle:
                            throttle.on_throttle()   # 减半并发度
                        logger.warning(
                            "[llm-concurrent] 传输异常，重入队末尾"
                            " key=%s retry=%d/%d",
                            task.key,
                            retries + 1,
                            max_task_retries,
                        )
                    else:
                        results[idx] = cr  # 超过重试上限，最终失败
                        logger.error(
                            "[llm-concurrent] 传输异常超过重入队上限"
                            " key=%s retries=%d",
                            task.key,
                            retries,
                        )

                else:
                    # 不可重试的错误（SCHEMA_ERROR、401、402 等）
                    results[idx] = cr

    finally:
        executor.shutdown(wait=False)

    return results


def dispatch_concurrent(
    tasks: list[ConcurrentTask[T]],
    call_fn: Callable[[T], Any],
    *,
    throttle: AdaptiveThrottle | None = None,
    first_sync: bool = True,
    first_done_event: threading.Event | None = None,
    ttft_timeout: float = 10.0,
    max_jitter_ms: int = 300,
    max_task_retries: int = 2,
) -> list[ConcurrentResult[T]]:
    """并发执行多个任务，返回与 ``tasks`` 顺序一致的结果列表。

    调度模型：工作队列 + 动态并发度。
    失败的传输异常任务放回队列末尾，等其他连接释放后重试；
    ``call_fn`` 内部重试（如 call_structured 的传输重试）处理瞬时抖动，
    本层的重入队机制处理持续性连接问题。

    Args:
        tasks: 任务列表。``first_sync=True`` 时 index 0 为首任务。
        call_fn: 同步调用函数，接受 task.payload，返回结果值。
        throttle: 自适应并发控制器。None 时不限速（最多 8 并发）。
        first_sync: True 时首任务同步执行，等待 first_done_event 后再启动其余任务。
        first_done_event: 首任务执行过程中由调用方 set 的信号（TTFT 到达时）。
        ttft_timeout: 等待 first_done_event 的超时秒数。
        max_jitter_ms: 任务启动前的最大随机延迟（毫秒），避免同时发起。
        max_task_retries: 每个任务最多重入队次数（传输异常时）。

    Returns:
        ConcurrentResult 列表，顺序与 tasks 一致。
        单个任务最终失败不影响其他任务。
    """
    if not tasks:
        return []

    if len(tasks) == 1:
        return [_run_one_safe(tasks[0], call_fn, throttle)]

    def _initial_workers(n: int) -> int:
        limit = throttle.max_concurrency if throttle else 8
        return max(1, min(n, limit))

    if not first_sync:
        results = _run_queue(
            list(enumerate(tasks)),
            _initial_workers(len(tasks)),
            call_fn,
            throttle,
            max_jitter_ms,
            max_task_retries,
        )
        return [results[i] for i in range(len(tasks))]

    # ── first_sync=True：首任务同步执行，等待 TTFT 后再启动其余任务 ──
    first_res = _run_one_safe(tasks[0], call_fn, throttle)

    if first_done_event is not None:
        if not first_done_event.wait(timeout=ttft_timeout):
            logger.warning(
                "[llm-concurrent] 首任务 TTFT 超时 %.1fs，继续并发其余任务",
                ttft_timeout,
            )
    # first_done_event 为 None 时，首任务已同步跑完，无需再等待

    rest = tasks[1:]
    if not rest:
        return [first_res]

    rest_results = _run_queue(
        [(i + 1, t) for i, t in enumerate(rest)],
        _initial_workers(len(rest)),
        call_fn,
        throttle,
        max_jitter_ms,
        max_task_retries,
    )

    all_results: dict[int, ConcurrentResult[T]] = {0: first_res, **rest_results}
    return [all_results[i] for i in range(len(tasks))]
