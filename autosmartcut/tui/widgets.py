"""tui/widgets.py — Textual Widget 组件。

包含：
- PipelineSidebar  侧边栏（节点状态）
- GenericStageView 通用阶段视图
- L1aProgressView  L1A 专用进度视图
- MainArea         主区域（切换视图）
- LogArea          日志区域
- CommandBar       底边命令栏（替代 Footer，支持退出确认模式）
- ReviewScreen     L2D 人工审阅界面（Widget，嵌入 MainArea）
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from autosmartcut.intelligence_2d_core import DisplayData
    from autosmartcut.pipeline_events import PipelineEvent

logger = logging.getLogger(__name__)

try:
    from textual.containers import VerticalScroll
    from textual.events import Key
    from textual.reactive import reactive
    from textual.screen import Screen
    from textual.widget import Widget
    from textual.widgets import Button, Input, RichLog, Static

    _TEXTUAL_AVAILABLE = True
except ImportError:
    _TEXTUAL_AVAILABLE = False


if _TEXTUAL_AVAILABLE:
    from autosmartcut.formatters import (
        format_decision_list,
        format_progress,
        format_review_summary,
        format_stats,
        parse_l1a_chunk_done,
        parse_l1a_intra_chunk_progress,
    )

    # -----------------------------------------------------------------------
    # PipelineSidebar
    # -----------------------------------------------------------------------

    class PipelineSidebar(Widget):
        """侧边栏：显示各节点的运行状态。"""

        _NODE_LABELS: dict[str, str] = {
            "l1_perception":     "L1 识别与对齐",
            "l2a_comprehension": "L2A 理解",
            "l2b_decision":      "L2B 决策",
            "l2c_review":        "L2C 审核",
            "l2d_human":         "L2D 人工",
            "l3_execute":        "L3 执行",
        }
        _STATUS_ICONS: dict[str, str] = {
            "pending":   "○",
            "running":   "→",
            "completed": "✓",
            "failed":    "✗",
            "skipped":   "⊘",
        }

        def compose(self):
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
    # GenericStageView
    # -----------------------------------------------------------------------

    class GenericStageView(Widget):
        """通用阶段视图：RichLog 承载历史行，底部 Static 显示当前进度。"""

        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._current_text: str = ""

        def compose(self):
            yield RichLog(id="generic-log", max_lines=200, wrap=True, auto_scroll=True)
            yield Static("", id="generic-current")

        def append_frozen(self, line: str) -> None:
            if line:
                try:
                    self.query_one("#generic-log", RichLog).write(line)
                except Exception:
                    pass

        def set_current(self, line: str) -> None:
            self._current_text = line
            try:
                self.query_one("#generic-current", Static).update(line)
            except Exception:
                pass

        def freeze_current(self) -> None:
            if self._current_text:
                self.append_frozen(self._current_text)
            self.set_current("")

    # -----------------------------------------------------------------------
    # L1aProgressView
    # -----------------------------------------------------------------------

    class L1aProgressView(Widget):
        """L1A 专用进度视图：三区分离（进度条 / 识别文本 / 块状态）。"""

        def compose(self):
            yield Static("", id="l1a-progress-bar")
            yield RichLog(id="l1a-text-log", max_lines=200, wrap=True, auto_scroll=True)
            yield Static("", id="l1a-chunk-status")

        def on_mount(self) -> None:
            self._texts: list[str] = []

        def on_resize(self) -> None:
            self._redraw_text_log()

        def _redraw_text_log(self) -> None:
            try:
                log = self.query_one("#l1a-text-log", RichLog)
                log.clear()
                if not self._texts:
                    return
                for text in self._texts[:-1]:
                    log.write(text)
                log.write(f"[green]{self._texts[-1]}[/green]")
                log.scroll_end(animate=False)
            except Exception:
                pass

        def handle_progress(self, event: "PipelineEvent") -> None:
            phase = event.phase
            payload = event.payload

            if phase == "asr_intra_chunk_progress":
                s = parse_l1a_intra_chunk_progress(payload)
                bar_width = 20
                filled = int(s.pct / 100 * bar_width)
                bar = "█" * filled + "░" * (bar_width - filled)
                speed = f" {s.speed_str}" if s.speed_str else ""
                line = f"  [{s.chunk_id+1}/{s.total_chunks}] {bar} {s.pct:.0f}%  剩余 {s.remain_str}{speed}"
                try:
                    self.query_one("#l1a-progress-bar", Static).update(line)
                except Exception:
                    pass

            elif phase == "asr_chunk_done":
                s = parse_l1a_chunk_done(payload)
                if s.text:
                    self._texts.append(s.text)
                    self._redraw_text_log()
                try:
                    self.query_one("#l1a-chunk-status", Static).update("")
                except Exception:
                    pass

            else:
                status_line = format_progress(event.node_id, event.phase, event.payload)
                try:
                    self.query_one("#l1a-chunk-status", Static).update(status_line)
                except Exception:
                    pass

    # -----------------------------------------------------------------------
    # MainArea
    # -----------------------------------------------------------------------

    class MainArea(Widget):
        """主区域：根据活跃节点切换视图。"""

        def compose(self):
            yield GenericStageView(id="generic-view")

        def show_stage_progress(self, node_id: str) -> None:
            if node_id == "l1_perception":
                self._switch_to_l1a_view()
            else:
                self._ensure_generic_view()
                try:
                    gv = self.query_one("#generic-view", GenericStageView)
                    gv.set_current(f"→ {node_id}")
                except Exception:
                    pass

        def show_stage_summary(
            self, node_id: str, summary: str, elapsed_sec: float = 0.0
        ) -> None:
            elapsed_str = f" ({elapsed_sec:.1f}s)" if elapsed_sec > 0 else ""
            summary_line = f"✓ {node_id}{elapsed_str}"
            if node_id == "l1_perception":
                self._teardown_l1a_view(summary_line)
            else:
                try:
                    gv = self.query_one("#generic-view", GenericStageView)
                    gv.freeze_current()
                    gv.append_frozen(summary_line)
                    gv.set_current("")
                except Exception:
                    pass

        def handle_node_progress(self, event: "PipelineEvent") -> None:
            if event.node_id == "l1_perception":
                try:
                    self.query_one("#l1a-view", L1aProgressView).handle_progress(event)
                except Exception:
                    pass
            else:
                try:
                    gv = self.query_one("#generic-view", GenericStageView)
                    gv.set_current(format_progress(event.node_id, event.phase, event.payload))
                except Exception:
                    pass

        def _switch_to_l1a_view(self) -> None:
            try:
                gv = self.query_one("#generic-view", GenericStageView)
                gv.display = False
            except Exception:
                pass
            existing = self.query("#l1a-view")
            if not existing:
                try:
                    self.mount(L1aProgressView(id="l1a-view"))
                except Exception:
                    pass

        def _teardown_l1a_view(self, summary_line: str) -> None:
            try:
                lv = self.query_one("#l1a-view", L1aProgressView)
                lv.remove()
            except Exception:
                pass
            self._ensure_generic_view()
            try:
                gv = self.query_one("#generic-view", GenericStageView)
                gv.append_frozen(summary_line)
            except Exception:
                pass

        def _ensure_generic_view(self) -> None:
            try:
                gv = self.query_one("#generic-view", GenericStageView)
                gv.display = True
            except Exception:
                pass

        def show_error(self, node_id: str, error: str) -> None:
            self._ensure_generic_view()
            try:
                gv = self.query_one("#generic-view", GenericStageView)
                gv.freeze_current()
                gv.append_frozen(f"[错误] {node_id}: {error}")
                gv.set_current("")
            except Exception:
                pass

        def show_complete(self, output: str) -> None:
            self._ensure_generic_view()
            try:
                gv = self.query_one("#generic-view", GenericStageView)
                gv.freeze_current()
                gv.append_frozen(f"✓ 完成 → {output}")
                gv.set_current("")
            except Exception:
                pass

        def show_review_screen(
            self,
            display: "DisplayData | None",
            on_action: Callable,
        ) -> None:
            """在主区域显示 ReviewScreen（2d 人工审阅界面）。"""
            try:
                for old in self.query(ReviewScreen):
                    old.remove()
                try:
                    self.query_one("#generic-view", GenericStageView).display = False
                except Exception:
                    pass
                review = ReviewScreen(display=display, on_action=on_action)
                self.mount(review)
            except Exception as e:
                logger.warning("show_review_screen 失败: %s", e)

    # -----------------------------------------------------------------------
    # LogArea
    # -----------------------------------------------------------------------

    class LogArea(Widget):
        """日志区域：可滚动，显示最近日志。"""

        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._log_screen_ref: "RichLog | None" = None

        def compose(self):
            yield RichLog(id="log-rich", max_lines=2000, wrap=True)

        def append_log(self, level: str, node_id: str, message: str) -> None:
            prefix = f"[{level}] " if level not in ("INFO", "") else ""
            node_prefix = f"[{node_id}] " if node_id else ""
            text = f"{prefix}{node_prefix}{message}"
            try:
                log_widget = self.query_one("#log-rich", RichLog)
                log_widget.write(text)
            except Exception:
                pass
            if self._log_screen_ref is not None:
                try:
                    self._log_screen_ref.write(text)
                except Exception:
                    self._log_screen_ref = None

    # -----------------------------------------------------------------------
    # ReviewScreen（Widget，嵌入 MainArea，用于 L2D 人工审阅）
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # CommandBar（替代 Footer，底边命令栏）
    # -----------------------------------------------------------------------

    class CommandBar(Widget, can_focus=True):
        """底边命令栏，替代 Textual 原生 Footer。

        正常模式：显示快捷键提示（p 暂停 / l 日志 / q 退出）。
        退出确认模式：显示退出选项，等待单键输入。
        颜色：黄棕色，类似 Vim statusline。
        """

        DEFAULT_CSS = """
        CommandBar {
            dock: bottom;
            height: 1;
            background: #5f4b00;
            color: #e8d5a0;
            layout: horizontal;
            padding: 0 1;
        }
        CommandBar.-confirm {
            background: #7a5f00;
        }
        CommandBar > Static {
            height: 1;
            background: transparent;
            color: #e8d5a0;
        }
        CommandBar > Static.key-hint {
            background: #3a2e00;
            color: #ffd700;
            text-style: bold;
            padding: 0 1;
            margin-right: 1;
            width: auto;
        }
        CommandBar > Static.key-desc {
            color: #e8d5a0;
            margin-right: 2;
            width: auto;
        }
        CommandBar > Static#confirm-bar {
            color: #ffd700;
            width: 1fr;
        }
        """

        _confirm_mode: reactive[bool] = reactive(False)

        def __init__(self, ctrl: "AppController | None" = None, **kwargs) -> None:  # type: ignore[name-defined]
            super().__init__(**kwargs)
            self._ctrl = ctrl

        def compose(self):
            # 正常模式：三组快捷键提示
            yield Static("p", classes="key-hint", id="hint-p-key")
            yield Static("暂停", classes="key-desc", id="hint-p-desc")
            yield Static("l", classes="key-hint", id="hint-l-key")
            yield Static("日志", classes="key-desc", id="hint-l-desc")
            yield Static("q", classes="key-hint", id="hint-q-key")
            yield Static("退出", classes="key-desc", id="hint-q-desc")
            # 确认模式：单行提示（默认隐藏）
            yield Static(
                "退出? [f]强制  [s]保存退出  [g]等待完成  [r]重配  [Esc]取消",
                id="confirm-bar",
            )

        def on_mount(self) -> None:
            self._set_mode(False)

        def _set_mode(self, confirm: bool) -> None:
            """切换正常/确认模式的显示状态。"""
            normal_ids = ["hint-p-key", "hint-p-desc", "hint-l-key",
                          "hint-l-desc", "hint-q-key", "hint-q-desc"]
            for wid in normal_ids:
                try:
                    self.query_one(f"#{wid}", Static).display = not confirm
                except Exception:
                    pass
            try:
                self.query_one("#confirm-bar", Static).display = confirm
            except Exception:
                pass
            self.set_class(confirm, "-confirm")

        def enter_confirm_mode(self) -> None:
            """进入退出确认模式，抢占键盘焦点。"""
            self._confirm_mode = True
            self._set_mode(True)
            self.focus()

        def exit_confirm_mode(self) -> None:
            """退出确认模式，恢复正常显示。"""
            self._confirm_mode = False
            self._set_mode(False)

        def on_key(self, event: Key) -> None:
            if not self._confirm_mode:
                return
            event.stop()
            key = event.key
            ctrl = self._ctrl
            if key == "escape":
                self.exit_confirm_mode()
            elif key == "f":
                self.exit_confirm_mode()
                if ctrl:
                    self.app._force_exit = True
                    ctrl.abort(save=False)
                    self.app.exit()
            elif key == "s":
                self.exit_confirm_mode()
                if ctrl:
                    self.app._force_exit = True
                    ctrl.abort(save=True)
                    self.app.exit()
            elif key == "g":
                self.exit_confirm_mode()
                if ctrl:
                    ctrl.pause()
                    self.app._graceful_quit = True
            elif key == "r":
                self.exit_confirm_mode()
                if ctrl:
                    try:
                        ctrl.reconfigure()
                        # AppController 状态变为 DIAGNOSING
                        # PipelineApp._on_ctrl_state_change 会自动 push ResumeScreen
                    except Exception as e:
                        logger.warning("CommandBar reconfigure 失败: %s", e)

    # -----------------------------------------------------------------------
    # ReviewScreen（Widget，嵌入 MainArea，用于 L2D 人工审阅）
    # -----------------------------------------------------------------------

    class ReviewScreen(Widget):
        """2d 人工审阅界面，嵌入主区域。"""

        def __init__(
            self,
            display: "DisplayData | None",
            on_action: Callable,
            **kwargs,
        ) -> None:
            super().__init__(**kwargs)
            self._display = display
            self._on_action = on_action

        def compose(self):
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
            dd = self._display
            if dd is None:
                return
            try:
                self.query_one("#goal-header", Static).update(
                    f"目标: {dd.goal}\n主旨: {dd.comprehension.get('purpose', '')}"
                )
                self.query_one("#review-summary", Static).update(
                    format_review_summary(dd.review_report)
                )
                self.query_one("#decision-list", Static).update(
                    format_decision_list(dd, use_markup=True)
                )
                self.query_one("#stats-bar", Static).update(
                    format_stats(dd.stats)
                )
            except Exception as e:
                logger.warning("ReviewScreen._refresh_display 失败: %s", e)

        def update_display(self, display: "DisplayData") -> None:
            self._display = display
            self._refresh_display()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            from autosmartcut.intelligence_2d_shell import HELP_TEXT, parse_command
            from autosmartcut.tui.screens import LogScreen

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
                    self.app.push_screen(LogScreen())
                except Exception as e:
                    logger.warning("show_log 推屏失败: %s", e)
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

else:
    # Textual 不可用时提供占位类
    class PipelineSidebar:  # type: ignore[no-redef]
        pass

    class GenericStageView:  # type: ignore[no-redef]
        pass

    class L1aProgressView:  # type: ignore[no-redef]
        pass

    class MainArea:  # type: ignore[no-redef]
        pass

    class LogArea:  # type: ignore[no-redef]
        pass

    class CommandBar:  # type: ignore[no-redef]
        pass

    class ReviewScreen:  # type: ignore[no-redef]
        pass
