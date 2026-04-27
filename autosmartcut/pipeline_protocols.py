"""pipeline_protocols.py — 流水线协议定义与自定义异常。

包含：
- StageNode Protocol（runtime_checkable）
- Scheduler Protocol（runtime_checkable）
- CyclicDependencyError
- MissingManifestFieldError
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from autosmartcut.pipeline_models import (
        PipelineSnapshot,
        SchedulerAction,
        StageContext,
        StageOutput,
        StageResult,
    )


# ---------------------------------------------------------------------------
# 自定义异常
# ---------------------------------------------------------------------------

class CyclicDependencyError(Exception):
    """DAG 中存在环路时抛出。"""


class MissingManifestFieldError(Exception):
    """节点所需的 manifest 字段不存在时抛出。"""


# ---------------------------------------------------------------------------
# StageNode Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class StageNode(Protocol):
    """流水线阶段节点协议。所有 8 个节点必须实现此协议。"""

    id: str
    """节点唯一标识符，如 "l1_perception"、"l2b_decision"。"""

    reads: "frozenset[str]"
    """节点从 TimelineManifest 读取的字段名集合。
    PipelineSession 在调度前验证这些字段已存在。"""

    writes: "frozenset[str]"
    """节点向 TimelineManifest 写入的字段名集合。
    用于 DAG 依赖推导：若节点 A 的 writes 与节点 B 的 reads 有交集，则 A → B。"""

    phase: int
    """节点所属阶段：1（感知层）、2（智能层）、3（执行层）。
    用于 --stage 过滤。"""

    resumable: bool
    """是否支持断点续跑。
    True：若 manifest.layer_status 中有完成标记，跳过执行。
    False（如 l1_perception）：始终重新执行。"""

    async def run(self, ctx: "StageContext") -> "StageResult":
        """节点主逻辑。异步执行，通过 ctx 读写 manifest 并发布事件。"""
        ...

    def summarize(self, manifest: dict) -> "StageOutput":
        """节点完成后生成摘要，供 PipelineSnapshot 和日志使用。"""
        ...


# ---------------------------------------------------------------------------
# Scheduler Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Scheduler(Protocol):
    """调度策略协议。PipelineSession 通过此协议委托调度决策。

    MVP 实现：FixedScheduler（按 DAG 拓扑序 + 硬编码循环规则）。
    未来实现：AgentScheduler（LLM Agent 自主决策）。
    """

    async def next_action(self, snapshot: "PipelineSnapshot") -> "SchedulerAction":
        """根据当前流水线快照，返回下一步调度动作。

        Preconditions:
            - snapshot.schedulable_nodes 非空，或流水线已完成
        Postconditions:
            - 返回的 SchedulerAction 中 node_ids 均在 snapshot.schedulable_nodes 中
            - 或返回 COMPLETE/PAUSE 动作
        """
        ...
