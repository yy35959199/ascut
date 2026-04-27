"""pipeline_models.py — 流水线核心数据结构与枚举定义。

包含：
- StageStatus / StageResult / StageContext
- NodeState / PipelineSnapshot
- SchedulerActionType / SchedulerAction
- 各节点 StageOutput 数据类及联合类型
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Callable, Union

if TYPE_CHECKING:
    import asyncio
    from autosmartcut.config import AppConfig
    from autosmartcut.pipeline_events import PipelineEvent


# ---------------------------------------------------------------------------
# StageStatus 枚举
# ---------------------------------------------------------------------------

class StageStatus(Enum):
    SUCCESS = "success"
    """节点成功完成，writes 字段已写入 manifest。"""

    FAILED = "failed"
    """节点执行失败，error 字段包含错误信息。"""

    NEEDS_INPUT = "needs_input"
    """节点需要人工输入（仅 l2d_human）。
    PipelineSession 发布 need_input 事件并等待 send_action()。"""

    REFLOW = "reflow"
    """节点请求回流（仅 l2d_human）。
    reflow_target 指定需要重置并重新调度的目标节点 id。"""


# ---------------------------------------------------------------------------
# StageResult
# ---------------------------------------------------------------------------

@dataclass
class StageResult:
    """节点 run() 方法的返回值。"""

    status: StageStatus
    """执行状态。"""

    summary: str = ""
    """人类可读的执行摘要，用于日志和 PipelineSnapshot。"""

    display: object = None
    """NEEDS_INPUT 状态时携带的展示数据，供消费层渲染 ReviewScreen。"""

    reflow_target: str = ""
    """REFLOW 状态时指定的目标节点 id。
    合法值："l2a_comprehension"（F1/F2 回流）或 "l2b_decision"（F3 回流）。"""

    error: "Exception | None" = None
    """FAILED 状态时的异常对象。"""


# ---------------------------------------------------------------------------
# StageContext
# ---------------------------------------------------------------------------

@dataclass
class StageContext:
    """节点运行时上下文，由 PipelineSession 在调度节点时构造并传入。"""

    manifest: dict
    """当前 TimelineManifest 的内存副本（可读写）。
    节点通过此字段读取 reads 声明的键，写入 writes 声明的键。"""

    config: "AppConfig"
    """全局配置（来自 config.toml）。"""

    emit: "Callable[[PipelineEvent], None]"
    """事件发布回调。节点调用此方法向 EventBus 发布 progress/log 事件。
    示例：ctx.emit(ProgressEvent(node_id=self.id, message="ASR 转录中..."))"""

    params: dict = field(default_factory=dict)
    """调度器注入的运行时参数（如 review_round、two_b_mode、manifest_path 等）。
    每个节点拿到独立副本，不再通过 manifest["_params"] 共享传递，避免并发写入竞争。"""

    pending_action: "asyncio.Queue | None" = None
    """仅 l2d_human 节点使用。PipelineSession 在调度 l2d_human 时注入此队列，
    节点通过 await pending_action.get() 等待用户操作。"""

    stage_filter: "frozenset[int] | None" = None
    """当前运行的阶段过滤集合，节点可据此决定是否跳过某些子步骤。"""


# ---------------------------------------------------------------------------
# NodeState / PipelineSnapshot
# ---------------------------------------------------------------------------

@dataclass
class NodeState:
    """单个节点的运行状态快照。"""

    node_id: str
    status: str  # "pending" / "running" / "completed" / "failed" / "skipped"
    output: "StageOutput | None" = None
    completed_at: "datetime | None" = None


@dataclass
class PipelineSnapshot:
    """供 Scheduler 做决策的结构化状态快照。
    PipelineSession 在每次需要调度决策时构造此对象并传给 Scheduler。"""

    node_states: "dict[str, NodeState]"
    """所有节点的当前状态，key 为 node_id。"""

    manifest_keys: "frozenset[str]"
    """当前 TimelineManifest 中已存在的顶层键集合。
    Scheduler 可据此判断哪些数据已就绪。"""

    schedulable_nodes: "list[str]"
    """当前可调度的节点 id 列表（所有前置节点已完成且自身为 pending）。"""

    reflow_count: int
    """本次流水线运行中已发生的回流次数。"""

    review_round: int
    """当前 2b→2c 循环的轮次（0-based）。"""

    last_review_verdict: str
    """最近一次 l2c_review 的 verdict："pass" 或 "fix_decision"。"""

    stage_filter: "frozenset[int] | None"
    """当前的阶段过滤集合。"""


# ---------------------------------------------------------------------------
# SchedulerActionType / SchedulerAction
# ---------------------------------------------------------------------------

class SchedulerActionType(Enum):
    RUN_NODE = "run_node"
    """调度单个节点立即执行。"""

    RUN_BATCH = "run_batch"
    """调度一批节点并行执行。"""

    PAUSE = "pause"
    """暂停流水线（当前节点完成后停止）。"""

    COMPLETE = "complete"
    """流水线已完成，无更多节点需要调度。"""


@dataclass
class SchedulerAction:
    """Scheduler.next_action() 的返回值。"""

    action_type: SchedulerActionType

    node_ids: "list[str]" = field(default_factory=list)
    """RUN_NODE 或 RUN_BATCH 时指定要调度的节点 id 列表。"""

    params: dict = field(default_factory=dict)
    """附加参数，如 review_round、two_b_mode 等，由 PipelineSession 注入 StageContext。"""


# ---------------------------------------------------------------------------
# StageOutput 各节点摘要数据类
# ---------------------------------------------------------------------------

@dataclass
class L1Output:
    """L1Node 完成摘要（识别 + 对齐）。"""
    annotation_count: int
    aligned_count: int
    raw_text_length: int
    duration_seconds: float


@dataclass
class L2aOutput:
    """L2aNode 完成摘要。"""
    purpose: str         # 截断到 80 字符
    block_count: int
    correction_count: int


@dataclass
class L2bOutput:
    """L2bNode 完成摘要。"""
    keep_count: int
    cut_count: int
    total: int
    review_round: int    # 当前是第几轮（0-based，由 FixedScheduler 传入）


@dataclass
class L2cOutput:
    """L2cNode 完成摘要。"""
    verdict: str         # "pass" 或 "fix_decision"
    must_pass_rate: str  # 如 "3/4"
    fix_count: int


@dataclass
class L2dOutput:
    """L2dNode 完成摘要。"""
    final_keep_count: int
    final_cut_count: int
    override_count: int
    reflow_count: int


@dataclass
class L3Output:
    """L3Node 完成摘要。"""
    output_video: str
    segment_count: int
    duration_seconds: float


# 联合类型
StageOutput = Union[
    L1Output,
    L2aOutput,
    L2bOutput,
    L2cOutput,
    L2dOutput,
    L3Output,
]
