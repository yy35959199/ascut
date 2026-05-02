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
        """诊断界面：展示 ProgressReport，提供操作选择。

        复用场景：
        1. 首次打开清单文件（AppController.open() → DIAGNOSING）
        2. 执行中途重配（AppController.reconfigure() → DIAGNOSING）

        用户选择操作后调 ctrl.confirm_resume()，pop screen。

        操作语义：
        - 重跑按钮（重跑 L3 / 重跑 L2+L3 / 全部重跑）：resume_mode=False，无条件执行指定阶段
        - 从中断处继续：resume_mode=True，跳过已完成节点，从未完成处接续
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
            yield Static("Goal:", id="goal-label")
            yield Input(
                value=self._report.goal if self._report else "",
                placeholder="剪辑意图（重跑 L2 时需要）",
                id="goal-input",
            )
            yield Static("操作：", id="action-label")
            yield Button("重跑 L3",     id="btn-rerun-3",   variant="primary")
            yield Button("重跑 L2+L3",  id="btn-rerun-23",  variant="default")
            yield Button("全部重跑",    id="btn-rerun-123", variant="default")
            yield Button("从中断处继续", id="btn-resume",    variant="success")
            yield Button("取消",        id="btn-cancel",    variant="default")
            yield Footer()

        def on_mount(self) -> None:
            self._refresh_display()
            self._update_button_states()

        def _update_button_states(self) -> None:
            """根据进度报告设置按钮可用性。"""
            report = self._report
            if report is None:
                return

            completed_ids = {n.node_id for n in report.nodes if n.completed}
            has_l1 = "l1_perception" in completed_ids
            has_l2d = "l2d_human" in completed_ids

            # "重跑 L3"：需要 L1 和 L2 都已完成（有 annotations + keep_mask）
            try:
                self.query_one("#btn-rerun-3", Button).disabled = not has_l2d
            except Exception:
                pass

            # "重跑 L2+L3"：需要 L1 已完成（有 annotations）
            try:
                self.query_one("#btn-rerun-23", Button).disabled = not has_l1
            except Exception:
                pass

            # "全部重跑"：需要源视频可达
            try:
                self.query_one("#btn-rerun-123", Button).disabled = not report.has_input_video_accessible
            except Exception:
                pass

            # "从中断处继续"：仅在有未完成节点时可用
            try:
                self.query_one("#btn-resume", Button).disabled = report.all_completed
            except Exception:
                pass

        def _refresh_display(self) -> None:
            report = self._report
            if report is None:
                return
            try:
                self.query_one("#resume-header", Static).update(
                    f"清单: {report.manifest_path}\n"
                    f"  run_id : {report.run_id}\n"
                    f"  视频   : {report.source_video}"
                )
                lines = []
                for n in report.nodes:
                    icon = "✓" if n.completed else "✗"
                    at = f"  {n.completed_at[:19]}" if n.completed_at else ""
                    lines.append(f"  {icon} {n.display_name:<20}{at}  {n.summary}")
                self.query_one("#resume-progress", Static).update("\n".join(lines))
                if report.warnings:
                    warn_text = "\n".join(f"  ⚠ {w}" for w in report.warnings)
                    self.query_one("#resume-warnings", Static).update(warn_text)
            except Exception as e:
                logger.warning("ResumeScreen._refresh_display 失败: %s", e)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            goal = ""
            try:
                goal = self.query_one("#goal-input", Input).value.strip()
            except Exception:
                pass

            match event.button.id:
                case "btn-rerun-3":
                    self._execute(stage="3", goal=goal, resume_mode=False)
                case "btn-rerun-23":
                    self._execute(stage="23", goal=goal, resume_mode=False)
                case "btn-rerun-123":
                    self._execute(stage="123", goal=goal, resume_mode=False)
                case "btn-resume":
                    stage = (self._report.suggested_stage or "3") if self._report else "3"
                    self._execute(stage=stage, goal=goal, resume_mode=True)
                case "btn-cancel":
                    self.app.pop_screen()

        def action_cancel(self) -> None:
            self.app.pop_screen()

        def _execute(self, stage: str, goal: str, resume_mode: bool) -> None:
            try:
                self._ctrl.confirm_resume(stage=stage, goal=goal, resume_mode=resume_mode)
                self.app.pop_screen()
                self._ctrl.start_pipeline()
            except Exception as e:
                logger.warning("ResumeScreen 执行失败: %s", e)
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
                        # reconfigure 后状态变为 DIAGNOSING，UI 层自己推 ResumeScreen
                        self.app.pop_screen()
                        from autosmartcut.tui.screens import ResumeScreen
                        self.app.push_screen(ResumeScreen(self._ctrl))
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
