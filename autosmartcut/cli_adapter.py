"""cli_adapter.py — CLI 适配器：将 EventBus 事件格式化为文本打印到标准输出。

供 ascut run（无交互模式）使用。
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autosmartcut.pipeline_events import PipelineEvent
    from autosmartcut.pipeline_session import PipelineSession


class CLIAdapter:
    """CLI 适配器：将 EventBus 事件格式化为文本打印到标准输出。
    供 ascut run（无交互模式）使用。
    """

    def __init__(self, session: "PipelineSession") -> None:
        self._session = session
        session.subscribe(self._handle_event)

    def _handle_event(self, event: "PipelineEvent") -> None:
        """将事件格式化为文本并打印。"""
        match event.type:
            case "stage_enter":
                print(f"[{event.timestamp.strftime('%H:%M:%S')}] → 开始: {event.node_id}")
            case "stage_exit":
                status_icon = "✓" if event.status == "success" else "✗"
                elapsed = f" ({event.elapsed_sec:.1f}s)" if event.elapsed_sec else ""
                print(
                    f"[{event.timestamp.strftime('%H:%M:%S')}] "
                    f"{status_icon} 完成: {event.node_id}{elapsed} — {event.summary}"
                )
            case "progress":
                text = self._format_progress(event)
                if text:
                    print(text)
            case "log":
                if event.level in ("WARNING", "ERROR"):
                    print(f"  [{event.level}] {event.message}", file=sys.stderr)
            case "need_input":
                # CLI 模式下 need_input 不应出现（L2dNode 无 pending_action 时自动确认）
                # 若出现，打印警告并自动确认
                print("  [警告] CLI 模式收到 need_input 事件，自动确认")
                from autosmartcut.intelligence_2d_core import AcceptAction
                self._session.send_action(AcceptAction())
            case "error":
                print(f"  [错误] {event.node_id}: {event.error}", file=sys.stderr)
            case "paused":
                print(f"  [暂停] 已完成节点: {', '.join(event.completed_nodes)}")
            case "pipeline_complete":
                print(f"\n=== 完成 → {event.output} ===")

    def _format_progress(self, event: "PipelineEvent") -> str:
        """将结构化 ProgressEvent 格式化为人类可读文本。"""
        from autosmartcut.formatters import format_progress
        return format_progress(event.node_id, event.phase, event.payload)

    def start_sync(self) -> None:
        """同步启动流水线。内部调用 session.start_sync()。"""
        self._session.start_sync()
