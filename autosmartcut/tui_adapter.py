"""tui_adapter.py — TUI 适配器：基于 Textual 框架。

将 EventBus 事件映射到 Textual 组件更新，提供三区域布局：
  侧边栏（流水线进度）| 主区域（当前阶段 / ReviewScreen）| 日志区域
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from autosmartcut.intelligence_2d_core import DisplayData
    from autosmartcut.pipeline_events import PipelineEvent
    from autosmartcut.pipeline_session import PipelineSession

logger = logging.getLogger(__name__)

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, VerticalScroll
    from textual.screen import Screen
    from textual.widget import Widget
    from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static

    _TEXTUAL_AVAILABLE = True
except ImportError:
    _TEXTUAL_AVAILABLE = False
    logger.warning("Textual 未安装，TUIAdapter 不可用（pip install textual）")


# ---------------------------------------------------------------------------
# TUIAdapter
# ---------------------------------------------------------------------------

class TUIAdapter:
    """TUI 适配器：基于 Textual 框架，将 EventBus 事件映射到 Textual 组件更新。"""

    def __init__(self, session: "PipelineSession") -> None:
        self._session = session
        self._app: "PipelineApp | None" = None
        session.subscribe(self._handle_event)

    def _handle_event(self, event: "PipelineEvent") -> None:
        """将事件转发给 Textual App（线程安全）。"""
        if self._app is not None:
            try:
                self._app.call_from_thread(self._app.handle_pipeline_event, event)
            except Exception as e:
                logger.warning("TUIAdapter._handle_event 转发失败: %s", e)

    async def start_async(self) -> None:
        """异步启动 TUI。同时运行 Textual App 和 PipelineSession。"""
        if not _TEXTUAL_AVAILABLE:
            raise RuntimeError("Textual 未安装，无法启动 TUI 模式（pip install textual）")
        self._app = PipelineApp(self._session)
        await asyncio.gather(
            self._app.run_async(),
            self._session.start_async(),
        )


# ---------------------------------------------------------------------------
# PipelineApp
# ---------------------------------------------------------------------------

if _TEXTUAL_AVAILABLE:

    class PipelineApp(App):
        """Textual App 主体：三区域布局。"""

        TITLE = "AutoSmartCut TUI"
        CSS = """
        PipelineSidebar {
            width: 24;
            border-right: solid $primary;
            padding: 1;
        }
        MainArea {
            width: 1fr;
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
        """

        BINDINGS = [
            Binding("p", "pause", "暂停"),
            Binding("q", "quit_app", "退出"),
        ]

        def __init__(self, session: "PipelineSession") -> None:
            super().__init__()
            self._session = session

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal():
                yield PipelineSidebar(id="sidebar")
                yield MainArea(id="main-area")
            yield LogArea(id="log-area")
            yield Footer()

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
                    main_area.show_stage_summary(event.node_id, event.summary)
                case "progress":
                    main_area.update_progress(event.node_id, event.message)
                case "log":
                    log_area.append_log(event.level, event.node_id, event.message)
                case "need_input":
                    main_area.show_review_screen(event.display, self._session.send_action)
                case "error":
                    main_area.show_error(event.node_id, event.error)
                    log_area.append_log("ERROR", event.node_id, event.error)
                case "paused":
                    self.push_screen(PauseDialog(session=self._session))
                case "pipeline_complete":
                    main_area.show_complete(event.output)

        def action_pause(self) -> None:
            """P 键触发暂停对话框。"""
            self.push_screen(PauseDialog(session=self._session))

        def action_quit_app(self) -> None:
            """Q 键退出（触发 abort）。"""
            self._session.abort(save=False)
            self.exit()

    # -----------------------------------------------------------------------
    # PipelineSidebar
    # -----------------------------------------------------------------------

    class PipelineSidebar(Widget):
        """侧边栏：显示 8 个节点的运行状态。"""

        _NODE_LABELS: dict[str, str] = {
            "l1a_asr":          "L1A ASR",
            "l1b_align":        "L1B 对齐",
            "l3_precompute":    "L3 预计算",
            "l2a_comprehension":"L2A 理解",
            "l2b_decision":     "L2B 决策",
            "l2c_review":       "L2C 审核",
            "l2d_human":        "L2D 人工",
            "l3_execute":       "L3 执行",
        }
        _STATUS_ICONS: dict[str, str] = {
            "pending":   "○",
            "running":   "→",
            "completed": "✓",
            "failed":    "✗",
            "skipped":   "⊘",
        }

        def compose(self) -> ComposeResult:
            yield Static("流水线进度", classes="sidebar-title")
            for node_id, label in self._NODE_LABELS.items():
                yield Static(
                    f"○ {label}",
                    id=f"node-{node_id}",
                    classes="node-pending",
                )

        def update_node_status(self, node_id: str, status: str) -> None:
            label = self._NODE_LABELS.get(node_id, node_id)
            icon = self._STATUS_ICONS.get(status, "?")
            try:
                widget = self.query_one(f"#node-{node_id}", Static)
                widget.update(f"{icon} {label}")
                widget.remove_class(
                    "node-pending", "node-running",
                    "node-completed", "node-failed", "node-skipped",
                )
                widget.add_class(f"node-{status}")
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # MainArea
    # -----------------------------------------------------------------------

    class MainArea(Widget):
        """主区域：显示当前阶段进度，need_input 时切换到 ReviewScreen。"""

        def compose(self) -> ComposeResult:
            yield Static("等待流水线启动...", id="main-content")

        def show_stage_progress(self, node_id: str) -> None:
            try:
                self.query_one("#main-content", Static).update(f"正在执行: {node_id}")
            except Exception:
                pass

        def show_stage_summary(self, node_id: str, summary: str) -> None:
            try:
                self.query_one("#main-content", Static).update(
                    f"完成: {node_id}\n{summary}"
                )
            except Exception:
                pass

        def update_progress(self, node_id: str, message: str) -> None:
            try:
                self.query_one("#main-content", Static).update(
                    f"[{node_id}] {message}"
                )
            except Exception:
                pass

        def show_error(self, node_id: str, error: str) -> None:
            try:
                self.query_one("#main-content", Static).update(
                    f"[错误] {node_id}: {error}"
                )
            except Exception:
                pass

        def show_complete(self, output: str) -> None:
            try:
                self.query_one("#main-content", Static).update(
                    f"✓ 完成\n输出: {output}"
                )
            except Exception:
                pass

        def show_review_screen(
            self,
            display: "DisplayData | None",
            on_action: Callable,
        ) -> None:
            """在主区域显示 ReviewScreen（2d 人工审阅界面）。"""
            try:
                # 移除旧的 ReviewScreen（若有）
                for old in self.query(ReviewScreen):
                    old.remove()
                # 隐藏 main-content
                try:
                    self.query_one("#main-content", Static).display = False
                except Exception:
                    pass
                # 挂载新的 ReviewScreen
                review = ReviewScreen(display=display, on_action=on_action)
                self.mount(review)
            except Exception as e:
                logger.warning("show_review_screen 失败: %s", e)

    # -----------------------------------------------------------------------
    # LogArea
    # -----------------------------------------------------------------------

    class LogArea(Widget):
        """日志区域：可滚动，显示最近 100 条 log 事件。"""

        def compose(self) -> ComposeResult:
            yield RichLog(id="log-rich", max_lines=100, wrap=True)

        def append_log(self, level: str, node_id: str, message: str) -> None:
            try:
                log_widget = self.query_one("#log-rich", RichLog)
                prefix = f"[{level}] " if level not in ("INFO", "") else ""
                log_widget.write(f"{prefix}[{node_id}] {message}")
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # ReviewScreen（Widget，嵌入 MainArea）
    # -----------------------------------------------------------------------

    class ReviewScreen(Widget):
        """2d 人工审阅界面，嵌入主区域。

        复用 intelligence_2d_shell.py 中的格式化函数，
        通过 on_action 回调调用 session.send_action()。
        """

        def __init__(
            self,
            display: "DisplayData | None",
            on_action: Callable,
            **kwargs,
        ) -> None:
            super().__init__(**kwargs)
            self._display = display
            self._on_action = on_action

        def compose(self) -> ComposeResult:
            with VerticalScroll():
                yield Static(id="goal-header")
                yield Static(id="review-summary")
                yield Static(id="decision-list")
            yield Static(id="stats-bar")
            yield Static(id="message-bar")
            yield Input(
                placeholder="命令: t/f1/f2/f3/f4/a/q/?",
                id="cmd-input",
            )

        def on_mount(self) -> None:
            self._refresh_display()
            try:
                self.query_one("#cmd-input", Input).focus()
            except Exception:
                pass

        def _refresh_display(self) -> None:
            """刷新显示内容（复用 intelligence_2d_shell 的格式化函数）。"""
            from autosmartcut.intelligence_2d_shell import (
                _format_decision_list,
                _format_review_summary,
                _format_stats,
            )
            dd = self._display
            if dd is None:
                return
            try:
                self.query_one("#goal-header", Static).update(
                    f"目标: {dd.goal}\n主旨: {dd.comprehension.get('purpose', '')}"
                )
                self.query_one("#review-summary", Static).update(
                    _format_review_summary(dd.review_report)
                )
                self.query_one("#decision-list", Static).update(
                    _format_decision_list(dd)
                )
                self.query_one("#stats-bar", Static).update(
                    _format_stats(dd.stats)
                )
            except Exception as e:
                logger.warning("ReviewScreen._refresh_display 失败: %s", e)

        def update_display(self, display: "DisplayData") -> None:
            """更新展示数据并刷新界面（由 NeedInputEvent 触发）。"""
            self._display = display
            self._refresh_display()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            """解析命令并通过 on_action 回调传递给 PipelineSession。"""
            from autosmartcut.intelligence_2d_shell import HELP_TEXT, parse_command

            raw = event.value.strip()
            try:
                self.query_one("#cmd-input", Input).value = ""
            except Exception:
                pass
            if not raw:
                return

            parsed = parse_command(raw)

            if parsed == "show_help":
                try:
                    self.query_one("#message-bar", Static).update(HELP_TEXT)
                except Exception:
                    pass
                return

            if parsed == "show_log":
                try:
                    self.query_one("#message-bar", Static).update(
                        "（TUI 模式下日志显示在底部区域）"
                    )
                except Exception:
                    pass
                return

            if parsed is None:
                try:
                    self.query_one("#message-bar", Static).update(
                        f"无效命令: {raw}  (输入 ? 查看帮助)"
                    )
                except Exception:
                    pass
                return

            self._on_action(parsed)

    # -----------------------------------------------------------------------
    # PauseDialog
    # -----------------------------------------------------------------------

    class PauseDialog(Screen):
        """暂停对话框，提供三个选项。"""

        def __init__(self, session: "PipelineSession", **kwargs) -> None:
            super().__init__(**kwargs)
            self._session = session

        def compose(self) -> ComposeResult:
            yield Label("流水线暂停选项：")
            yield Button("取消（继续执行）", id="btn-cancel", variant="default")
            yield Button("强制中止并保存", id="btn-abort-save", variant="warning")
            yield Button(
                "等待当前阶段完成后暂停",
                id="btn-pause-graceful",
                variant="primary",
            )

        def on_button_pressed(self, event: Button.Pressed) -> None:
            match event.button.id:
                case "btn-cancel":
                    self.app.pop_screen()
                case "btn-abort-save":
                    self._session.abort(save=True)
                    self.app.exit()
                case "btn-pause-graceful":
                    self._session.pause()
                    self.app.pop_screen()

else:
    # Textual 不可用时提供占位类，避免 ImportError
    class PipelineApp:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise RuntimeError("Textual 未安装，无法使用 TUI 模式")

    class PipelineSidebar:  # type: ignore[no-redef]
        pass

    class MainArea:  # type: ignore[no-redef]
        pass

    class LogArea:  # type: ignore[no-redef]
        pass

    class ReviewScreen:  # type: ignore[no-redef]
        pass

    class PauseDialog:  # type: ignore[no-redef]
        pass
