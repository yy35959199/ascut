"""tui/screens.py — Textual Screen 组件。

包含：
- ResumeScreen  诊断界面：展示进度报告，提供参数选择
- QuitDialog    退出对话框（含"修改参数重跑"选项）
- PauseDialog   暂停对话框

注意：LogScreen 已移至 tui/widgets.py（与 LogArea 同文件，消除互指）。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autosmartcut.cli.app_controller import AppController
    from autosmartcut.manifest.manifest_progress import ProgressReport
    from autosmartcut.pipeline.pipeline_session import PipelineSession

logger = logging.getLogger(__name__)

try:
    from textual.app import ComposeResult
    from textual.binding import Binding
    from textual.containers import VerticalScroll
    from textual.screen import Screen
    from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static

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
            yield Static("强制重跑 (Force Rerun):", id="force-rerun-label")
            yield Input(
                value="",
                placeholder="留空=续跑；填 2 或 23 = 从头重跑对应 phase",
                id="force-rerun-input",
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
                force_rerun_raw = self.query_one("#force-rerun-input", Input).value.strip()
            except Exception:
                stage = "123"
                goal = ""
                force_rerun_raw = ""

            # 解析 force_rerun_phases
            force_rerun_phases: "frozenset[int] | None" = None
            if force_rerun_raw:
                try:
                    phases: set[int] = set()
                    for ch in force_rerun_raw:
                        if ch not in ("1", "2", "3"):
                            raise ValueError(f"非法字符: {ch!r}，只接受 1/2/3 的组合")
                        phases.add(int(ch))
                    force_rerun_phases = frozenset(phases) if phases else None
                except ValueError as e:
                    try:
                        self.query_one("#resume-warnings", Static).update(f"错误: {e}")
                    except Exception:
                        pass
                    return

            try:
                self._ctrl.confirm_resume(stage=stage, goal=goal, force_rerun_phases=force_rerun_phases)
                self.app.pop_screen()
            except Exception as e:
                logger.warning("ResumeScreen confirm_resume 失败: %s", e)
                try:
                    self.query_one("#resume-warnings", Static).update(f"错误: {e}")
                except Exception:
                    pass

    # -----------------------------------------------------------------------
    # QuitDialog（增加"修改参数重跑"选项）
    # -----------------------------------------------------------------------

    class QuitDialog(Screen):
        """退出对话框：提供五种选项（含修改参数重跑）。"""

        def __init__(self, ctrl: "AppController", **kwargs) -> None:
            super().__init__(**kwargs)
            self._ctrl = ctrl

        def compose(self) -> ComposeResult:
            yield Label("退出选项：")
            yield Button("取消（继续执行）", id="btn-cancel", variant="default")
            yield Button("强制退出（不保存）", id="btn-force-quit", variant="error")
            yield Button("保存并退出", id="btn-save-quit", variant="warning")
            yield Button(
                "等待当前阶段完成后退出",
                id="btn-graceful-quit",
                variant="primary",
            )
            yield Button(
                "修改参数重跑",
                id="btn-reconfigure",
                variant="default",
            )

        def on_button_pressed(self, event: Button.Pressed) -> None:
            match event.button.id:
                case "btn-cancel":
                    self.app.pop_screen()
                case "btn-force-quit":
                    self.app._force_exit = True
                    self._ctrl.abort(save=False)
                    self.app.exit()
                case "btn-save-quit":
                    self.app._force_exit = True
                    self._ctrl.abort(save=True)
                    self.app.exit()
                case "btn-graceful-quit":
                    self._ctrl.pause()
                    self.app._graceful_quit = True
                    self.app.pop_screen()
                case "btn-reconfigure":
                    try:
                        self._ctrl.reconfigure()
                        # AppController 状态变为 DIAGNOSING
                        # PipelineApp._on_ctrl_state_change 会自动 push ResumeScreen
                        self.app.pop_screen()
                    except Exception as e:
                        logger.warning("reconfigure 失败: %s", e)
                        self.app.pop_screen()

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

    class QuitDialog:  # type: ignore[no-redef]
        pass

    class PauseDialog:  # type: ignore[no-redef]
        pass
