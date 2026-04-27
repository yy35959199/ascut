"""tui/screens.py — Textual Screen 组件。

包含：
- ResumeScreen  诊断界面：展示进度报告，提供参数选择
- LogScreen     全屏日志界面
- PauseDialog   暂停对话框
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autosmartcut.app_controller import AppController
    from autosmartcut.manifest_progress import ProgressReport
    from autosmartcut.pipeline_session import PipelineSession

logger = logging.getLogger(__name__)

try:
    from textual.app import ComposeResult
    from textual.binding import Binding
    from textual.containers import VerticalScroll
    from textual.screen import Screen
    from textual.widgets import Button, Header, Input, Label, RichLog, Static

    _TEXTUAL_AVAILABLE = True
except ImportError:
    _TEXTUAL_AVAILABLE = False


if _TEXTUAL_AVAILABLE:

    # -----------------------------------------------------------------------
    # ResumeScreen（新增）
    # -----------------------------------------------------------------------

    class ResumeScreen(Screen):
        """诊断界面：展示 ProgressReport，提供参数选择。

        复用场景：
        1. 首次打开清单文件（AppController.open() → DIAGNOSING）
        2. 执行中途重配（AppController.reconfigure() → DIAGNOSING）

        用户确认后调 ctrl.confirm_resume()，pop screen。
        """

        BINDINGS = [Binding("escape", "cancel", "取消", show=True)]

        def __init__(self, ctrl: "AppController", **kwargs) -> None:
            super().__init__(**kwargs)
            self._ctrl = ctrl
            self._report = ctrl.progress_report

        def compose(self) -> ComposeResult:
            yield Header()
            with VerticalScroll():
                yield Static(id="resume-header")
                yield Static(id="resume-progress")
                yield Static(id="resume-warnings")
            yield Static("Stage:", id="stage-label")
            yield Input(
                value=self._report.suggested_stage or "123" if self._report else "123",
                placeholder="stage（如 3、23、123）",
                id="stage-input",
            )
            yield Static("Goal:", id="goal-label")
            yield Input(
                value=self._report.goal if self._report else "",
                placeholder="剪辑意图（L2 需要）",
                id="goal-input",
            )
            yield Button("继续执行", id="btn-confirm", variant="primary")
            yield Button("取消", id="btn-cancel", variant="default")
            yield Footer()

        def on_mount(self) -> None:
            self._refresh_display()
            try:
                self.query_one("#stage-input", Input).focus()
            except Exception:
                pass

        def _refresh_display(self) -> None:
            report = self._report
            if report is None:
                return
            try:
                # 标题
                self.query_one("#resume-header", Static).update(
                    f"清单: {report.manifest_path}\n"
                    f"  run_id : {report.run_id}\n"
                    f"  视频   : {report.source_video}"
                )
                # 进度状态
                lines = []
                for n in report.nodes:
                    icon = "✓" if n.completed else "✗"
                    at = f"  {n.completed_at[:19]}" if n.completed_at else ""
                    lines.append(f"  {icon} {n.display_name:<20}{at}  {n.summary}")
                if report.suggested_stage:
                    lines.append(f"\n建议继续: --stage {report.suggested_stage}")
                self.query_one("#resume-progress", Static).update("\n".join(lines))
                # 警告
                if report.warnings:
                    warn_text = "\n".join(f"  ⚠ {w}" for w in report.warnings)
                    self.query_one("#resume-warnings", Static).update(warn_text)
            except Exception as e:
                logger.warning("ResumeScreen._refresh_display 失败: %s", e)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            match event.button.id:
                case "btn-confirm":
                    self._do_confirm()
                case "btn-cancel":
                    self.action_cancel()

        def action_cancel(self) -> None:
            self.app.pop_screen()

        def _do_confirm(self) -> None:
            try:
                stage = self.query_one("#stage-input", Input).value.strip() or "123"
                goal = self.query_one("#goal-input", Input).value.strip()
            except Exception:
                stage = "123"
                goal = ""

            try:
                self._ctrl.confirm_resume(stage=stage, goal=goal)
                self.app.pop_screen()
            except Exception as e:
                logger.warning("ResumeScreen confirm_resume 失败: %s", e)
                try:
                    self.query_one("#resume-warnings", Static).update(f"错误: {e}")
                except Exception:
                    pass

    # -----------------------------------------------------------------------
    # LogScreen
    # -----------------------------------------------------------------------

    class LogScreen(Screen):
        """全屏日志界面。通过 L 键推入，Esc 返回。"""

        BINDINGS = [Binding("escape", "app.pop_screen", "返回", show=True)]

        def compose(self) -> ComposeResult:
            yield Header()
            yield RichLog(id="log-screen-rich", max_lines=2000, wrap=True)
            yield Footer()

        def on_mount(self) -> None:
            from autosmartcut.tui.widgets import LogArea
            try:
                log_area = self.app.query_one("#log-area", LogArea)
                src = log_area.query_one("#log-rich", RichLog)
                dst = self.query_one("#log-screen-rich", RichLog)
                lines = getattr(src, "_lines", None)
                if lines:
                    for line in lines:
                        dst.write(line)
                dst.scroll_end(animate=False)
                log_area._log_screen_ref = dst
            except Exception as e:
                logger.warning("LogScreen.on_mount 复制日志失败: %s", e)

        def on_unmount(self) -> None:
            from autosmartcut.tui.widgets import LogArea
            try:
                log_area = self.app.query_one("#log-area", LogArea)
                log_area._log_screen_ref = None
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # PauseDialog
    # -----------------------------------------------------------------------

    class PauseDialog(Screen):
        """暂停对话框，提供三个选项。"""

        def __init__(self, ctrl: "AppController", **kwargs) -> None:
            super().__init__(**kwargs)
            self._ctrl = ctrl

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
                    self.app._force_exit = True
                    self._ctrl.abort(save=True)
                    self.app.exit()
                case "btn-pause-graceful":
                    self._ctrl.pause()
                    self.app.pop_screen()

else:
    class ResumeScreen:  # type: ignore[no-redef]
        pass

    class LogScreen:  # type: ignore[no-redef]
        pass

    class PauseDialog:  # type: ignore[no-redef]
        pass
