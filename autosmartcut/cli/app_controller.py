"""app_controller.py — 应用控制器层。

提供两个控制器类：

- ``SessionController``：session 生命周期管理，``ascut run`` 使用的最小单元。
- ``AppController``：在 ``SessionController`` 基础上增加状态机和回调机制，
  TUI / GUI / WebUI 使用。

设计约定：
- 两个类均不含任何 UI 框架代码（无 Textual、无 Qt）。
- Pipeline 线程管理委托给 ``PipelineThread``（pipeline/pipeline_thread.py）。
- ``_set_state`` 只更新内部状态，不通知 UI。
- ``_on_session_event`` 在 pipeline 线程里被调用，通过 poster 跨线程通知 UI。
- UI 层发起的操作（open/confirm_resume）由 UI 层自己处理后续逻辑。
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
    from autosmartcut.pipeline.pipeline_thread import PipelineThread
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
        """暂停流水线（当前节点完成后停止）。线程安全（只设标志）。"""
        if self._session is not None:
            self._session.pause()

    def abort(self, save: bool = True) -> None:
        """中止流水线。save=True 时保存已完成阶段的检查点。线程安全。"""
        if self._session is not None:
            self._session.abort(save=save)


# ---------------------------------------------------------------------------
# AppController
# ---------------------------------------------------------------------------

class AppController(SessionController):
    """交互式应用控制器，TUI / GUI / WebUI 使用。

    在 ``SessionController`` 基础上增加：
    - 应用状态机（AppState）
    - 输入类型判断（open()）
    - 用户确认参数（confirm_resume()）
    - 执行中途重配（reconfigure()）
    - Pipeline 线程启动（start_pipeline()，委托给 PipelineThread）
    - UI 层事件投递接口（set_ui_poster / set_state_poster）

    线程模型：
    - Pipeline 在独立 daemon 线程里运行（PipelineThread 管理）。
    - UI 层通过 set_ui_poster / set_state_poster 注册投递方法。
    - poster 在 pipeline 线程里被调用，UI 层负责线程安全。
    - _set_state 只更新内部状态，不通知 UI（避免 UI 线程自己绕一圈）。
    - UI 层发起的操作（open/confirm_resume）由 UI 层自己处理后续逻辑。
    """

    def __init__(self) -> None:
        super().__init__()
        self._state: AppState = AppState.IDLE
        self._resolved_input: "ResolvedInput | None" = None
        self._progress_report: "ProgressReport | None" = None

        # Pipeline 线程（由 start_pipeline 创建）
        self._pipeline_thread: "PipelineThread | None" = None

        # UI 层投递接口（poster 在 pipeline 线程里被调用）
        self._ui_poster: Callable[["PipelineEvent"], None] | None = None
        self._state_poster: Callable[[AppState], None] | None = None

    # ── UI 层接口 ────────────────────────────────────────────────────────────

    def set_ui_poster(self, poster: Callable[["PipelineEvent"], None]) -> None:
        """注册 pipeline 事件投递方法。

        poster 在 pipeline 线程里被调用，实现方必须保证线程安全：
        - Textual: ``lambda ev: app.call_later(app.handle_pipeline_event, ev)``
        - Qt:      ``lambda ev: window.pipeline_event_signal.emit(ev)``
        - WebUI:   ``lambda ev: asyncio.run_coroutine_threadsafe(ws.send_json(...), ui_loop)``
        """
        self._ui_poster = poster

    def set_state_poster(self, poster: Callable[[AppState], None]) -> None:
        """注册状态变化投递方法。

        poster 在 pipeline 线程里被调用（仅 pipeline 线程触发的状态变化）。
        UI 线程发起的状态变化（open/confirm_resume）不经过 poster。
        """
        self._state_poster = poster

    # ── Pipeline 线程管理 ─────────────────────────────────────────────────────

    def start_pipeline(self) -> None:
        """在独立 daemon 线程里启动 pipeline。非阻塞，立即返回。

        pipeline 完成/失败通过 PipelineEvent 通知 UI。

        Raises:
            RuntimeError: session 未构造或 pipeline 线程已在运行。
        """
        from autosmartcut.pipeline.pipeline_thread import PipelineThread

        if self._session is None:
            raise RuntimeError("session 尚未构造，请先调用 setup()")
        if self._pipeline_thread is not None and self._pipeline_thread.is_alive:
            raise RuntimeError("pipeline 线程已在运行")

        self._pipeline_thread = PipelineThread(self._session)
        self._pipeline_thread.start()

    def send_action(self, action: object) -> None:
        """UI 线程调用，将用户操作投递到 pipeline 线程的 action_queue。

        委托给 PipelineThread.send_action，通过 call_soon_threadsafe 保证线程安全。
        """
        if self._pipeline_thread is not None:
            self._pipeline_thread.send_action(action)

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

        UI 层调用此方法后，自行根据 ctrl.state 决定下一步（不经过 poster）。

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

        UI 层调用此方法后，自行调用 start_pipeline()（不经过 poster）。

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

        self._setup_from_manifest(
            manifest_path, stage, goal,
            force_rerun_phases=force_rerun_phases,
            **overrides,
        )
        self._set_state(AppState.READY)

    def reconfigure(self) -> "ProgressReport":
        """执行中途重配：abort 当前 pipeline，等待线程结束，回到 DIAGNOSING。

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

        # 停止 pipeline 线程
        if self._pipeline_thread is not None:
            self._pipeline_thread.stop(timeout=10.0)
        self._pipeline_thread = None

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
        """订阅当前 session 的 EventBus。在 setup() 之后调用。"""
        if self._session is None:
            return
        self._session.subscribe(self._on_session_event)

    def _on_session_event(self, event: "PipelineEvent") -> None:
        """处理 session 事件：驱动状态转换 + 通过 poster 投递到 UI 线程。

        在 pipeline 线程（asyncio loop 或 call_soon_threadsafe 回调）中被调用。
        这是唯一调用 poster 的地方（跨线程通知）。
        """
        # 驱动状态转换（不触发任何 UI 操作，只更新内部状态）
        new_state: AppState | None = None
        match event.type:
            case "stage_enter":
                if self._state == AppState.READY:
                    new_state = AppState.RUNNING
            case "pipeline_complete":
                new_state = AppState.COMPLETED
            case "paused":
                new_state = AppState.PAUSED
            case "error":
                new_state = AppState.FAILED

        if new_state is not None and new_state != self._state:
            self._state = new_state
            logger.debug("AppController 状态: → %s", new_state.value)
            # 跨线程通知 UI
            if self._state_poster is not None:
                try:
                    self._state_poster(new_state)
                except Exception as e:
                    logger.warning("state_poster 异常: %s", e)

        # 投递事件到 UI 线程
        if self._ui_poster is not None:
            try:
                self._ui_poster(event)
            except Exception as e:
                logger.warning("ui_poster 异常: %s", e)

    # ── 内部辅助 ─────────────────────────────────────────────────────────────

    def _resolve_input(self, path: Path) -> "ResolvedInput":
        from autosmartcut.cli.input_resolver import resolve_input
        return resolve_input(path)

    def _setup_from_media(self, resolved: "ResolvedInput") -> None:
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
        """仅更新内部状态，不通知 UI。

        UI 线程发起的状态变化（open/confirm_resume）不需要通知 UI——
        UI 层自己知道它发起了什么操作，自己处理后续逻辑。
        跨线程通知只在 _on_session_event 里通过 poster 完成。
        """
        if self._state == new_state:
            return
        self._state = new_state
        logger.debug("AppController 状态: → %s", new_state.value)

    def setup(self, params: "PipelineParams") -> None:
        """构造 session 并订阅事件。覆盖父类方法以增加事件订阅。"""
        super().setup(params)
        self._subscribe_to_session()
