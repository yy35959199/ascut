"""tui/app.py — PipelineApp 主体。

接收 AppController，不直接持有 session。
线程模型：Pipeline 在独立 daemon 线程里运行（AppController.start_pipeline()），
Textual App 在主线程的 asyncio loop 里运行，两者通过 poster 回调通信。
"""
from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autosmartcut.cli.app_controller import AppController, AppState
    from autosmartcut.pipeline.pipeline_events import PipelineEvent

logger = logging.getLogger(__name__)

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal
    from textual.widgets import Footer, Header

    _TEXTUAL_AVAILABLE = True
except ImportError:
    _TEXTUAL_AVAILABLE = False
    logger.warning("Textual 未安装，PipelineApp 不可用（pip install textual）")


if _TEXTUAL_AVAILABLE:
    from autosmartcut.tui.logging.stream_hub import LogStreamHub
    from autosmartcut.tui.logging.repository import LogRepository
    from autosmartcut.tui.logging.context import RunLogContext
    from autosmartcut.tui.widgets import LogArea, LogScreen, MainArea, PipelineSidebar
    class PipelineApp(App):
        """Textual App 主体：三区域布局。

        接收 AppController，不直接持有 session。
        Pipeline 通过 AppController.start_pipeline() 在独立线程启动。
        事件通过 set_ui_poster / set_state_poster 投递到 Textual 主线程。
        """

        TITLE = "AutoSmartCut TUI"
        CSS = """
        PipelineSidebar {
            width: 24;
            border-right: solid $primary;
            padding: 1;
        }
        MainArea {
            width: 1fr;
            height: 1fr;
            padding: 1;
        }
        LogArea {
            height: 8;
            border-top: solid $primary;
            padding: 0 1;
        }
        .node-pending  { color: $text-muted; }
        .node-running  { color: $warning; }
        .node-completed { color: $success; }
        .node-failed   { color: $error; }
        .node-skipped  { color: $text-muted; }
        GenericStageView {
            height: 1fr;
            layout: vertical;
        }
        #generic-current {
            height: 1;
            color: $text-muted;
        }
        LLMStreamView {
            height: 1fr;
            layout: vertical;
            display: none;
        }
        #stream-status {
            height: 1;
            color: $warning;
        }
        #stream-progress {
            height: 1;
            color: $text-muted;
        }
        #stream-reasoning {
            height: 1fr;
            border: none;
            color: $text-muted;
        }
        #stream-reasoning-cur {
            height: 1;
            color: $text-muted;
        }
        #stream-divider {
            height: 1;
            color: $text-muted;
        }
        #stream-content {
            height: auto;
            max-height: 8;
            border: none;
        }
        #stream-content-cur {
            height: 1;
        }
        #stream-addon {
            height: auto;
            max-height: 14;
            border-top: solid $primary;
            margin-top: 1;
        }
        #addon-decisions-label {
            height: 1;
        }
        #addon-decisions-log {
            height: 1fr;
            max-height: 10;
            border: none;
        }
        #addon-result-header {
            height: 1;
        }
        #addon-result-table {
            height: auto;
            max-height: 12;
        }
        L1aProgressView {
            height: 1fr;
            layout: vertical;
        }
        #l1a-progress-bar {
            height: 2;
            color: $warning;
        }
        #l1a-text-log {
            height: 1fr;
            border: none;
        }
        #l1a-chunk-status {
            height: 1;
            color: $text-muted;
        }
        L3ProgressView {
            height: 1fr;
            layout: vertical;
        }
        #l3-header {
            height: 1;
            color: $text-muted;
            margin-bottom: 1;
        }
        #l3-log {
            height: 1fr;
            border: none;
        }
        ResumeScreen {
            background: $surface;
            padding: 1 2;
        }
        #resume-header {
            color: $text;
            margin-bottom: 1;
        }
        #resume-progress {
            color: $text;
            margin-bottom: 1;
        }
        #stage-label, #goal-label, #force-rerun-label {
            color: $text-muted;
        }
        """

        BINDINGS = [
            Binding("p", "pause", "暂停"),
            Binding("l", "show_log", "日志"),
            Binding("q", "quit_app", "退出"),
        ]

        def __init__(self, ctrl: "AppController") -> None:
            super().__init__()
            self._ctrl = ctrl
            self._loguru_sink_id: int | None = None
            self._log_hub = LogStreamHub()
            self._log_repository = LogRepository()
            self._force_exit: bool = False
            self._graceful_quit: bool = False
            self._original_stderr = None
            self._original_stdout = None
            self._devnull = None

            # 注册 UI 投递方法（pipeline 线程 → Textual 主线程）
            ctrl.set_ui_poster(self._post_pipeline_event)
            ctrl.set_state_poster(self._post_state_change)

        # ── poster 方法（从 pipeline 线程调用，线程安全）────────────────────

        def _post_pipeline_event(self, event: "PipelineEvent") -> None:
            """从 pipeline 线程投递事件到 Textual 主线程。"""
            self.call_later(self.handle_pipeline_event, event)

        def _post_state_change(self, state: "AppState") -> None:
            """从 pipeline 线程投递状态变化到 Textual 主线程。"""
            self.call_later(self._handle_state_change, state)

        def on_mount(self) -> None:
            """挂载时注册 loguru sink，重定向 stdout/stderr 到 devnull，根据状态决定初始界面。

            Textual 接管了终端 alternate screen buffer，任何第三方库直接写
            stdout/stderr 的输出都会覆盖 TUI 渲染。此处统一将两者重定向到
            os.devnull（而非 StringIO，避免内存泄漏），确保只有 Textual 能输出到终端。

            日志统一走 loguru TUI sink → LogStreamHub → LogArea / LogScreen。
            不再通过 EventBus LogEvent 路径（已删除）。
            """
            import os as _os

            # 保存原始 stdout/stderr，on_unmount 时恢复
            self._original_stdout = sys.stdout
            self._original_stderr = sys.stderr
            # 重定向到 devnull：吞掉所有第三方库的直接输出
            self._devnull = open(_os.devnull, "w", encoding="utf-8")
            sys.stdout = self._devnull
            sys.stderr = self._devnull

            from loguru import logger as loguru_logger

            def _tui_sink(message: object) -> None:
                text = str(message).rstrip("\n")
                self.call_later(self._publish_log_line, text)

            self._loguru_sink_id = loguru_logger.add(
                _tui_sink,
                level="INFO",
                format="{time:HH:mm:ss} | {level: <8} | {message}",
                colorize=False,
                enqueue=True,   # loguru 后台线程调 sink，工作线程不直接碰 Textual
            )

            # 根据控制器状态决定初始界面
            from autosmartcut.cli.app_controller import AppState
            if self._ctrl.state == AppState.READY:
                self._ctrl.start_pipeline()
            elif self._ctrl.state == AppState.DIAGNOSING:
                from autosmartcut.tui.screens import ResumeScreen
                self.push_screen(ResumeScreen(self._ctrl))

        def on_unmount(self) -> None:
            """卸载时恢复 stdout/stderr，清理 loguru sink，强制退出时清屏。"""
            real_stdout = getattr(self, "_original_stdout", None) or sys.__stdout__
            real_stderr = getattr(self, "_original_stderr", None) or sys.__stderr__
            sys.stdout = real_stdout
            sys.stderr = real_stderr
            self._original_stdout = None
            self._original_stderr = None
            devnull = getattr(self, "_devnull", None)
            if devnull is not None:
                try:
                    devnull.close()
                except Exception:
                    pass
                self._devnull = None
            if self._loguru_sink_id is not None:
                from loguru import logger as loguru_logger
                try:
                    loguru_logger.remove(self._loguru_sink_id)
                except Exception:
                    pass
                self._loguru_sink_id = None
            if self._force_exit:
                import os as _os
                real_stdout.write("\033[2J\033[H")
                real_stdout.flush()
                _os._exit(0)

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal():
                yield PipelineSidebar(id="sidebar")
                yield MainArea(id="main-area")
            yield LogArea(self._log_hub, id="log-area")
            yield Footer()

        # ── 状态变化处理（在 Textual 主线程中执行，仅处理来自 pipeline 线程的通知）──

        def _handle_state_change(self, state: "AppState") -> None:
            """处理来自 pipeline 线程的状态变化通知。

            UI 线程自己发起的操作（open/confirm_resume）不经过此方法——
            UI 层自己处理后续逻辑（直接调 start_pipeline 等）。
            """
            from autosmartcut.cli.app_controller import AppState
            match state:
                case AppState.RUNNING:
                    pass  # sidebar 已通过 stage_enter 事件更新，无需额外处理
                case AppState.COMPLETED:
                    try:
                        main_area = self.query_one("#main-area", MainArea)
                        main_area.show_complete("完成")
                    except Exception:
                        pass
                case AppState.PAUSED:
                    if self._graceful_quit:
                        self._force_exit = True
                        self.exit()
                    # 非 graceful_quit 的暂停由 pipeline_event "paused" 处理（弹 PauseDialog）
                case AppState.FAILED:
                    pass  # 错误已通过 pipeline_event "error" 展示
                case AppState.READY:
                    # reconfigure 后用户在 ResumeScreen 确认，confirm_resume 触发 READY
                    # 此时 poster 通知 UI 启动 pipeline
                    self._ctrl.start_pipeline()

        # ── pipeline 事件处理（在 Textual 主线程中执行）─────────────────────

        def handle_pipeline_event(self, event: "PipelineEvent") -> None:
            """处理来自 PipelineSession 的事件（在 Textual 主线程中调用）。"""
            try:
                sidebar = self.query_one("#sidebar", PipelineSidebar)
                main_area = self.query_one("#main-area", MainArea)
                log_area = self.query_one("#log-area", LogArea)
            except Exception:
                return

            match event.type:
                case "stage_enter":
                    sidebar.update_node_status(event.node_id, "running")
                    main_area.show_stage_progress(event.node_id)
                case "stage_exit":
                    status = "completed" if event.status == "success" else "failed"
                    sidebar.update_node_status(event.node_id, status)
                    main_area.show_stage_summary(
                        event.node_id,
                        event.summary,
                        elapsed_sec=getattr(event, "elapsed_sec", 0.0),
                    )
                case "progress":
                    main_area.handle_node_progress(event)
                case "need_input":
                    main_area.show_review_screen(event.display, self._ctrl.send_action)
                case "error":
                    main_area.show_error(event.node_id, event.error)
                    log_area.append_log("ERROR", event.node_id, event.error)
                case "paused":
                    if self._graceful_quit:
                        self._force_exit = True
                        self.exit()
                    else:
                        from autosmartcut.tui.screens import PauseDialog
                        self.push_screen(PauseDialog(ctrl=self._ctrl))
                case "pipeline_complete":
                    main_area.show_complete(event.output)

        # ── 内部方法 ─────────────────────────────────────────────────────────

        def _publish_log_line(self, text: str) -> None:
            """loguru sink 投递：发布到 Hub，由 LogArea / LogScreen 订阅显示。"""
            self._log_hub.publish(text)

        def make_log_screen(self) -> LogScreen:
            """构造全屏日志界面（供 l 键与 ReviewScreen 命令使用）。"""
            ctx = RunLogContext.from_app_controller(self._ctrl)
            return LogScreen(
                hub=self._log_hub,
                repository=self._log_repository,
                context=ctx,
            )

        # ── 键盘动作 ─────────────────────────────────────────────────────────

        def action_pause(self) -> None:
            from autosmartcut.tui.screens import PauseDialog
            self.push_screen(PauseDialog(ctrl=self._ctrl))

        def action_show_log(self) -> None:
            self.push_screen(self.make_log_screen())

        def action_quit_app(self) -> None:
            from autosmartcut.tui.screens import QuitDialog
            self.push_screen(QuitDialog(ctrl=self._ctrl))

else:
    class PipelineApp:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise RuntimeError("Textual 未安装，无法使用 TUI 模式（pip install textual）")
