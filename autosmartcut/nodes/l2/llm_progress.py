"""llm_progress.py — LLM 流式 chunk 到 EventBus 的桥接工厂。

职责：
- 提供 ``make_on_chunk`` 工厂函数，供 l2x_node 构造 on_chunk 回调
- 将 StreamChunk 翻译为 ProgressEvent(phase="llm_stream")，推入 EventBus
- payload 只含基础类型，不引入跨层类型依赖

层次边界：
- 本模块属于节点层（Layer 3），知道 StreamChunk 和 ProgressEvent
- 不知道 TUI/CLI 如何渲染，不知道 LLM 内部实现
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autosmartcut.nodes.l2.intelligence_llm import StreamChunk

logger = logging.getLogger(__name__)


def make_on_chunk(
    emit: Callable,
    node_id: str,
) -> Callable[["StreamChunk"], None]:
    """构造 on_chunk 回调，将 StreamChunk 桥接到 EventBus ProgressEvent。

    Args:
        emit: ``ctx.emit``，PipelineSession 注入的事件发布回调。
        node_id: 当前节点 ID（如 "l2a_comprehension"），写入 ProgressEvent.node_id。

    Returns:
        on_chunk 回调函数，传给 ``call_structured(on_chunk=...)``。

    payload 字段说明（与 StreamChunk 字段对应）：
    - ``stage``           : LLM 阶段标识（r1/r2/decision/review/light）
    - ``event``           : 事件类型（reasoning_delta/content_delta/usage/retry/result）
    - ``reasoning_delta`` : thinking 模式下推理过程增量文本（event=reasoning_delta 时非空）
    - ``content_delta``   : 最终回答增量文本（event=content_delta 时非空）
    - ``attempt``         : 重试次数（event=retry 时有效，1-based）
    - ``retry_reason``    : 重试原因简述（event=retry 时有效）

    usage 和 result 不放入 payload：
    - usage 由 _log_llm_call 在调用完成后记录到日志
    - result 由节点层自己处理（写 manifest + 发 stage_exit）
    """
    from autosmartcut.pipeline.pipeline_events import ProgressEvent

    def on_chunk(chunk: "StreamChunk") -> None:
        try:
            emit(ProgressEvent(
                node_id=node_id,
                phase="llm_stream",
                payload={
                    "stage":           chunk.stage,
                    "event":           chunk.event,
                    "reasoning_delta": chunk.reasoning_delta,
                    "content_delta":   chunk.content_delta,
                    "attempt":         chunk.attempt,
                    "retry_reason":    chunk.retry_reason,
                },
            ))
        except Exception as e:
            # emit 失败不应中断 LLM 收集
            logger.warning("[llm_progress] emit ProgressEvent 失败（忽略）: %s", e)

    return on_chunk
