"""tui/app.py — PipelineApp 主体。

接收 AppController，不直接持有 session。
asyncio 模型：App 独立运行，session 作为 task 在 App 内部按需启动。

原 TUIAdapter 的事件转发逻辑已合并至此。
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autosmartcut.app_controller import AppController, AppState
    from autosmartcut.pipeline_events import PipelineEvent

logger = logging.getLogger(__name__)

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal
    from textual.widgets import Header

    _TEXTUAL_AVAILABLE = True
except ImportError:
    _TEXTUAL_AVAILABLE = False
    logger.warning("Textual 未安装，PipelineApp 不可用（pip install textual）")


if _TEXTUAL_AVAILABLE:
    from autosmartcut.tui.widgets import CommandBar, LogArea, MainArea, PipelineSidebar

    class PipelineApp(App):
        """Textual App 主体：三区域布局。

        接收 AppController，不直接持有 session。
        session 在用户确认参数后（AppState.READY）通过 create_task 启动。
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
        #stage-label, #goal-label {
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
            self._pipeline_task: asyncio.Task | None = None
            self._loguru_sink_id: int | None = None
            self._force_exit: bool = False
            self._graceful_quit: bool = False
            self._original_stderr = None
            self._original_stdout = None
            self._devnull = None

            # 注册控制器回调
            ctrl.on_state_change(self._on_ctrl_state_change)
            ctrl.on_pipeline_event(self._on_pipeline_event)

        def on_mount(self) -> None:
            """挂载时注册 loguru sink，重定向 stdout/stderr 到 devnull，根据状态决定初始界面。

            Textual 接管了终端 alternate screen buffer，任何第三方库直接写
            stdout/stderr 的输出都会覆盖 TUI 渲染。此处统一将两者重定向到
            os.devnull（而非 StringIO，避免内存泄漏），确保只有 Textual 能输出到终端。

            日志通过两条路径展示：
            1. EventBus → AppController → PipelineApp.handle_pipeline_event
            2. loguru TUI sink → LogArea（此处注册）
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
                self.call_later(self._append_to_log_area, text)

            self._loguru_sink_id = loguru_logger.add(
                _tui_sink,
                level="INFO",
                format="{time:HH:mm:ss} | {level: <8} | {message}",
                colorize=False,
                enqueue=False,
            )

            # 根据控制器状态决定初始界面
            from autosmartcut.app_controller import AppState
            if self._ctrl.state == AppState.READY:
                self._start_pipeline()
            elif self._ctrl.state == AppState.DIAGNOSING:
                from autosmartcut.tui.screens import ResumeScreen
                self.push_screen(ResumeScreen(self._ctrl))

        def on_unmount(self) -> None:
            """卸载时恢复 stdout/stderr，清理 loguru sink，强制退出时清屏。"""
            # 恢复 stdout/stderr（必须在 _force_exit 清屏之前恢复）
            real_stdout = getattr(self, "_original_stdout", None) or sys.__stdout__
            real_stderr = getattr(self, "_original_stderr", None) or sys.__stderr__
            sys.stdout = real_stdout
            sys.stderr = real_stderr
            self._original_stdout = None
            self._original_stderr = None
            # 关闭 devnull 文件句柄
            devnull = getattr(self, "_devnull", None)
            if devnull is not None:
                try:
                    devnull.close()
                except Exception:
                    pass
                self._devnull = None
            # 移除 loguru TUI sink
            if self._loguru_sink_id is not None:
                from loguru import logger as loguru_logger
                try:
                    loguru_logger.remove(self._loguru_sink_id)
                except Exception:
                    pass
                self._loguru_sink_id = None
            # 强制退出路径
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
            yield LogArea(id="log-area")
            yield CommandBar(ctrl=self._ctrl, id="cmd-bar")

        # ── 控制器回调（在 asyncio 线程触发，通过 call_later 投递到 Textual 主线程）──

        def _on_ctrl_state_change(self, state: "AppState") -> None:
            """控制器状态变化 → 切换界面。"""
            self.call_later(self._handle_state_change, state)

        def _handle_state_change(self, state: "AppState") -> None:
            """在 Textual 主线程中处理状态变化。"""
            from autosmartcut.app_controller import AppState
            match state:
                case AppState.READY:
                    self._start_pipeline()
                case AppState.DIAGNOSING:
                    from autosmartcut.tui.screens import ResumeScreen
                    self.push_screen(ResumeScreen(self._ctrl))
                case AppState.COMPLETED:
                    try:
                        main_area = self.query_one("#main-area", MainArea)
                        main_area.show_complete("完成")
                    except Exception:
                        pass
                case AppState.FAILED:
                    pass  # 错误已通过 pipeline_event 展示

        def _on_pipeline_event(self, event: "PipelineEvent") -> None:
            """pipeline 事件 → 更新渲染（通过 call_later 保证线程安全）。"""
            self.call_later(self.handle_pipeline_event, event)

        # ── pipeline 事件处理 ────────────────────────────────────────────────

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
                case "log":
                    log_area.append_log(event.level, event.node_id, event.message)
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

        def _start_pipeline(self) -> None:
            """启动 session task。"""
            if not self._ctrl.is_ready:
                logger.warning("_start_pipeline: session 尚未就绪")
                return
            session = self._ctrl.session
            if self._pipeline_task is not None and not self._pipeline_task.done():
                logger.warning("_start_pipeline: 已有 pipeline task 在运行")
                return
            self._pipeline_task = asyncio.create_task(session.start_async())

        def _append_to_log_area(self, text: str) -> None:
            try:
                log_area = self.query_one("#log-area", LogArea)
                log_area.append_log("", "sys", text)
            except Exception:
                pass

        # ── 键盘动作 ─────────────────────────────────────────────────────────

        def action_pause(self) -> None:
            from autosmartcut.tui.screens import PauseDialog
            self.push_screen(PauseDialog(ctrl=self._ctrl))

        def action_show_log(self) -> None:
            from autosmartcut.tui.screens import LogScreen
            self.push_screen(LogScreen())

        def action_quit_app(self) -> None:
            try:
                cmd_bar = self.query_one("#cmd-bar", CommandBar)
                cmd_bar.enter_confirm_mode()
            except Exception as e:
                logger.warning("action_quit_app: 无法找到 CommandBar: %s", e)

else:
    class PipelineApp:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise RuntimeError("Textual 未安装，无法使用 TUI 模式（pip install textual）")
