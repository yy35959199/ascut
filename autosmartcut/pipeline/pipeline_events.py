"""pipeline_events.py — 流水线事件数据类定义。

所有事件均为不可变数据类，通过 EventBus 发布给消费层（CLIAdapter / TUIAdapter）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Union


@dataclass
class StageEnterEvent:
    """节点开始执行时发布。"""
    type: Literal["stage_enter"] = "stage_enter"
    node_id: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class StageExitEvent:
    """节点执行完成（无论成功或失败）时发布。"""
    type: Literal["stage_exit"] = "stage_exit"
    node_id: str = ""
    status: str = ""     # "success" / "failed" / "needs_input" / "reflow"
    summary: str = ""
    """人类可读摘要，保留用于 CLI 向后兼容。"""
    output: "object | None" = None
    """结构化摘要（StageOutput 子类），由 PipelineSession 调用 node.summarize() 填充。
    消费层可据此渲染节点完成后的详细信息，无需解析 summary 字符串。"""
    elapsed_sec: float = 0.0
    """节点从开始到完成的挂钟时间（秒），由 PipelineSession 填充。"""
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ProgressEvent:
    """节点执行过程中的进度更新事件。

    逻辑层只发结构化数据，不做任何格式化。
    消费层（CLI/TUI/WebUI）各自根据 node_id + phase + payload 决定如何呈现。
    """
    type: Literal["progress"] = "progress"
    node_id: str = ""
    phase: str = ""
    """节点内部阶段标识，如 "transcode_start"、"asr_chunk_done" 等。"""
    payload: dict = field(default_factory=dict)
    """结构化数据，键集合由各节点的 phase/payload 契约定义。"""
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class LogEvent:
    """节点或 PipelineSession 产生的日志事件。"""
    type: Literal["log"] = "log"
    node_id: str = ""
    level: str = "INFO"  # "DEBUG" / "INFO" / "WARNING" / "ERROR"
    message: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class NeedInputEvent:
    """节点需要人工输入时发布（仅 l2d_human）。"""
    type: Literal["need_input"] = "need_input"
    node_id: str = ""
    display: object = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ErrorEvent:
    """节点执行失败时发布。"""
    type: Literal["error"] = "error"
    node_id: str = ""
    error: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class PausedEvent:
    """流水线暂停时发布（pause() 调用后当前节点完成时）。"""
    type: Literal["paused"] = "paused"
    completed_nodes: "list[str]" = field(default_factory=list)
    checkpoint_path: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class PipelineCompleteEvent:
    """流水线全部完成时发布。"""
    type: Literal["pipeline_complete"] = "pipeline_complete"
    output: str = ""     # 输出视频路径（若运行了 L3）或 manifest 路径
    elapsed_seconds: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)


# 联合类型：所有可能的流水线事件
PipelineEvent = Union[
    StageEnterEvent,
    StageExitEvent,
    ProgressEvent,
    LogEvent,
    NeedInputEvent,
    ErrorEvent,
    PausedEvent,
    PipelineCompleteEvent,
]
