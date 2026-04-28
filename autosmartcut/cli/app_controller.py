"""app_controller.py — 应用控制器层。

提供两个控制器类：

- ``SessionController``：session 生命周期管理，``ascut run`` 使用的最小单元。
- ``AppController``：在 ``SessionController`` 基础上增加状态机和回调机制，
  TUI / GUI 使用。

设计约定：
- 两个类均不含任何 UI 框架代码（无 Textual、无 Qt）。
- ``AppController`` 的回调在 asyncio event loop 线程中触发，
  UI 层负责自己的线程安全（Textual 用 call_later，Qt 用信号槽）。
"""
from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from autosmartcut.config import AppConfig
    from autosmartcut.cli.input_resolver import ResolvedInput
    from autosmartcut.manifest.manifest_progress import ProgressReport
    from autosmartcut.pipeline.pipeline_events import PipelineEvent
    from autosmartcut.pipeline.pipeline_run import PipelineRun
    from autosmartcut.pipeline.pipeline_session import PipelineSession
    from autosmartcut.pipeline.session_factory import PipelineParams

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AppState
# ---------------------------------------------------------------------------

class AppState(Enum):
    """交互式应用的状态机状态。"""

    IDLE = "idle"
    """初始状态，尚未打开任何文件。"""

    DIAGNOSING = "diagnosing"
    """已打开清单文件，正在展示进度诊断，等待用户确认参数。"""

    READY = "ready"
    """参数已确认，session 已构造，可以开始执行。"""

    RUNNING = "running"
    """流水线正在执行中。"""

    PAUSED = "paused"
    """流水线已暂停（用户主动暂停或节点完成后暂停）。"""

    COMPLETED = "completed"
    """流水线全部完成。"""

    FAILED = "failed"
    """流水线执行失败。"""


# ---------------------------------------------------------------------------
# SessionController
# ---------------------------------------------------------------------------

class SessionController:
    """session 生命周期管理。

    ``ascut run`` 模式直接使用此类，不需要状态机。
    ``AppController`` 继承此类并增加状态机。
    """

    def __init__(self) -> None:
        self._run: "PipelineRun | None" = None
        self._session: "PipelineSession | None" = None
        self._cfg: "AppConfig | None" = None

    def setup(self, params: "PipelineParams") -> None:
        """构造 PipelineRun + PipelineSession。

        Args:
            params: 构造参数。

        Raises:
            FileNotFoundError: 清单或视频不存在。
            ValueError: 参数校验失败。
        """
        from autosmartcut.pipeline.session_factory import build_session
        self._run, self._session, self._cfg = build_session(params)

    @property
    def session(self) -> "PipelineSession":
        """当前 PipelineSession。setup() 之前访问会抛 RuntimeError。"""
        if self._session is None:
            raise RuntimeError("session 尚未构造，请先调用 setup()")
        return self._session

    @property
    def run(self) -> "PipelineRun":
        """当前 PipelineRun。setup() 之前访问会抛 RuntimeError。"""
        if self._run is None:
            raise RuntimeError("run 尚未构造，请先调用 setup()")
        return self._run

    @property
    def cfg(self) -> "AppConfig":
        """当前 AppConfig。setup() 之前访问会抛 RuntimeError。"""
        if self._cfg is None:
            raise RuntimeError("cfg 尚未构造，请先调用 setup()")
        return self._cfg

    @property
    def is_ready(self) -> bool:
        """session 是否已构造。"""
        return self._session is not None

    def pause(self) -> None:
        """暂停流水线（当前节点完成后停止）。"""
        if self._session is not None:
            self._session.pause()

    def abort(self, save: bool = True) -> None:
        """中止流水线。save=True 时保存已完成阶段的检查点。"""
        if self._session is not None:
            self._session.abort(save=save)

    def send_action(self, action: object) -> None:
        """向 l2d_human 节点发送用户操作。"""
        if self._session is not None:
            self._session.send_action(action)


# ---------------------------------------------------------------------------
# AppController
# ---------------------------------------------------------------------------

class AppController(SessionController):
    """交互式应用控制器，TUI / GUI 使用。

    在 ``SessionController`` 基础上增加：
    - 应用状态机（AppState）
    - 输入类型判断（open()）
    - 用户确认参数（confirm_resume()）
    - 执行中途重配（reconfigure()）
    - 状态变化回调（on_state_change）
    - pipeline 事件转发回调（on_pipeline_event）

    回调约定：所有回调在 asyncio event loop 线程中触发，
    UI 层负责自己的线程安全。
    """

    def __init__(self) -> None:
        super().__init__()
        self._state: AppState = AppState.IDLE
        self._resolved_input: "ResolvedInput | None" = None
        self._progress_report: "ProgressReport | None" = None
        self._state_callbacks: list[Callable[[AppState], None]] = []
        self._event_callbacks: list[Callable[["PipelineEvent"], None]] = []

    # ── 回调注册 ────────────────────────────────────────────────────────────

    def on_state_change(self, callback: Callable[[AppState], None]) -> None:
        """注册状态变化监听。

        回调在 asyncio event loop 线程中触发，UI 层负责线程安全。
        """
        self._state_callbacks.append(callback)

    def on_pipeline_event(self, callback: Callable[["PipelineEvent"], None]) -> None:
        """注册 pipeline 事件监听（转发 session EventBus）。

        回调在 asyncio event loop 线程中触发，UI 层负责线程安全。
        """
        self._event_callbacks.append(callback)

    # ── 属性 ────────────────────────────────────────────────────────────────

    @property
    def state(self) -> AppState:
        """当前应用状态。"""
        return self._state

    @property
    def progress_report(self) -> "ProgressReport | None":
        """当前进度报告（DIAGNOSING 状态时有值）。"""
        return self._progress_report

    @property
    def resolved_input(self) -> "ResolvedInput | None":
        """当前解析的输入（open() 之后有值）。"""
        return self._resolved_input

    # ── 状态转换方法 ─────────────────────────────────────────────────────────

    def open(self, path: Path) -> "ResolvedInput":
        """打开文件/文件夹，判断类型，推断进度。

        - MEDIA_FILE  → setup(params) → state = READY
        - MANIFEST_*  → state = DIAGNOSING，附带 progress_report

        Args:
            path: 输入路径（媒体文件、清单文件或清单目录）。

        Returns:
            ResolvedInput 解析结果。

        Raises:
            FileNotFoundError: 路径不存在。
            ValueError: 路径类型无法识别。
        """
        from autosmartcut.cli.input_resolver import InputType

        resolved = self._resolve_input(path)
        self._resolved_input = resolved

        if resolved.input_type == InputType.MEDIA_FILE:
            self._setup_from_media(resolved)
        else:
            # 清单文件/目录：进入诊断状态
            self._progress_report = resolved.progress_report
            self._set_state(AppState.DIAGNOSING)

        return resolved

    def confirm_resume(
        self,
        stage: str,
        goal: str,
        force_rerun_phases: "frozenset[int] | None" = None,
        **overrides: object,
    ) -> None:
        """用户确认参数，构造 session，进入 READY 状态。

        适用场景：
        - 首次打开清单文件后用户确认参数
        - 执行中途重配后用户确认新参数

        Args:
            stage: stage 规格字符串（如 "3"、"23"、"123"）。
            goal: 剪辑意图。
            force_rerun_phases: 强制重跑的 phase 集合（如 frozenset({2})）。
            **overrides: 其他 PipelineParams 字段覆盖。
        """
        if self._resolved_input is None:
            raise RuntimeError("请先调用 open() 打开文件")

        manifest_path = self._resolved_input.manifest_path
        if manifest_path is None:
            raise RuntimeError("当前输入不是清单文件，无法续跑")

        self._setup_from_manifest(manifest_path, stage, goal, force_rerun_phases=force_rerun_phases, **overrides)
        self._set_state(AppState.READY)

    def reconfigure(self) -> "ProgressReport":
        """执行中途重配：abort 当前 session，回到 DIAGNOSING 状态。

        适用场景：用户在执行中途想修改参数重跑。

        Returns:
            最新 ProgressReport，供 UI 渲染诊断界面。

        Raises:
            RuntimeError: 当前状态不允许重配（非 RUNNING/PAUSED）。
        """
        from autosmartcut.manifest.manifest_io import load_manifest
        from autosmartcut.manifest.manifest_progress import infer_progress

        if self._state not in (AppState.RUNNING, AppState.PAUSED):
            raise RuntimeError(
                f"只能在 RUNNING 或 PAUSED 状态下重配，当前状态: {self._state}"
            )

        # abort 当前 session，保存已完成阶段的检查点
        self.abort(save=True)

        # 重新推断进度
        manifest_path = self._run.manifest_path if self._run else None
        if manifest_path is None:
            raise RuntimeError("无法获取清单路径")

        data = load_manifest(manifest_path)
        report = infer_progress(data, manifest_path)
        self._progress_report = report

        # 重置 session（保留 run，因为清单路径不变）
        self._session = None

        self._set_state(AppState.DIAGNOSING)
        return report

    # ── session 事件处理 ─────────────────────────────────────────────────────

    def _subscribe_to_session(self) -> None:
        """订阅当前 session 的 EventBus，驱动状态转换并转发事件给 UI。

        在 setup() 之后、session 开始执行之前调用。
        """
        if self._session is None:
            return
        self._session.subscribe(self._on_session_event)

    def _on_session_event(self, event: "PipelineEvent") -> None:
        """处理 session 事件：驱动状态转换 + 转发给 UI 层。

        在 asyncio event loop 线程中被调用。
        """
        # 驱动状态转换
        match event.type:
            case "stage_enter":
                if self._state == AppState.READY:
                    self._set_state(AppState.RUNNING)
            case "pipeline_complete":
                self._set_state(AppState.COMPLETED)
            case "paused":
                self._set_state(AppState.PAUSED)
            case "error":
                self._set_state(AppState.FAILED)

        # 转发给 UI 层
        for cb in self._event_callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.warning("AppController event callback 异常: %s", e)

    # ── 内部辅助 ─────────────────────────────────────────────────────────────

    def _resolve_input(self, path: Path) -> "ResolvedInput":
        """仅输入解析（resolve_input）。"""
        from autosmartcut.cli.input_resolver import resolve_input
        return resolve_input(path)

    def _setup_from_media(self, resolved: "ResolvedInput") -> None:
        """媒体文件场景：构造 PipelineParams 并 setup，进入 READY。"""
        from autosmartcut.pipeline.session_factory import PipelineParams

        if resolved.media_path is None:
            raise RuntimeError("内部错误：MEDIA_FILE 但 media_path 为空")
        params = PipelineParams(
            input_video=resolved.media_path,
            stage="123",
        )
        self.setup(params)
        self._set_state(AppState.READY)

    def _setup_from_manifest(
        self,
        manifest_path: Path,
        stage: str,
        goal: str,
        force_rerun_phases: "frozenset[int] | None" = None,
        **overrides: object,
    ) -> None:
        """清单续跑：组装 PipelineParams 并 setup（不改变 AppState）。"""
        from autosmartcut.pipeline.session_factory import PipelineParams

        params = PipelineParams(
            manifest_path=manifest_path,
            stage=stage,
            goal=goal,
            force_rerun_phases=force_rerun_phases,
            **overrides,  # type: ignore[arg-type]
        )
        self.setup(params)

    def _set_state(self, new_state: AppState) -> None:
        """设置新状态并触发回调。"""
        if self._state == new_state:
            return
        old_state = self._state
        self._state = new_state
        logger.debug("AppController 状态: %s → %s", old_state.value, new_state.value)
        for cb in self._state_callbacks:
            try:
                cb(new_state)
            except Exception as e:
                logger.warning("AppController state callback 异常: %s", e)

    def setup(self, params: "PipelineParams") -> None:
        """构造 session 并订阅事件。覆盖父类方法以增加事件订阅。"""
        super().setup(params)
        self._subscribe_to_session()
