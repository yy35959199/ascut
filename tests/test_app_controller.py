"""AppController 状态机单测。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autosmartcut.cli.app_controller import AppController, AppState, SessionController
from autosmartcut.pipeline.session_factory import PipelineParams


# ---------------------------------------------------------------------------
# SessionController 基础测试
# ---------------------------------------------------------------------------

class TestSessionController:
    def test_properties_before_setup_raise(self):
        ctrl = SessionController()
        with pytest.raises(RuntimeError):
            _ = ctrl.session
        with pytest.raises(RuntimeError):
            _ = ctrl.run
        with pytest.raises(RuntimeError):
            _ = ctrl.cfg

    def test_is_ready_false_before_setup(self):
        ctrl = SessionController()
        assert ctrl.is_ready is False

    def test_setup_calls_build_session(self):
        ctrl = SessionController()
        mock_run = MagicMock()
        mock_session = MagicMock()
        mock_cfg = MagicMock()

        with patch("autosmartcut.app_controller.SessionController.setup") as mock_setup:
            # 直接测试 setup 调用 build_session
            pass

        # 用 patch build_session 来测试
        with patch("autosmartcut.session_factory.build_session") as mock_build:
            mock_build.return_value = (mock_run, mock_session, mock_cfg)
            params = PipelineParams(manifest_path=Path("/fake/manifest.json"), stage="3")
            ctrl.setup(params)

        assert ctrl.is_ready is True
        assert ctrl._run is mock_run
        assert ctrl._session is mock_session
        assert ctrl._cfg is mock_cfg

    def test_pause_abort_send_action_safe_before_setup(self):
        """setup 之前调用 pause/abort/send_action 不应抛异常。"""
        ctrl = SessionController()
        ctrl.pause()   # 不应抛
        ctrl.abort()   # 不应抛
        ctrl.send_action(MagicMock())  # 不应抛


# ---------------------------------------------------------------------------
# AppController 状态机测试
# ---------------------------------------------------------------------------

class TestAppControllerState:
    def _make_ctrl(self) -> AppController:
        return AppController()

    def test_initial_state_is_idle(self):
        ctrl = self._make_ctrl()
        assert ctrl.state == AppState.IDLE

    def test_on_state_change_callback(self):
        ctrl = self._make_ctrl()
        states: list[AppState] = []
        ctrl.on_state_change(states.append)

        # 模拟状态转换
        ctrl._set_state(AppState.DIAGNOSING)
        ctrl._set_state(AppState.READY)
        ctrl._set_state(AppState.RUNNING)

        assert states == [AppState.DIAGNOSING, AppState.READY, AppState.RUNNING]

    def test_set_state_no_duplicate_callback(self):
        ctrl = self._make_ctrl()
        states: list[AppState] = []
        ctrl.on_state_change(states.append)

        ctrl._set_state(AppState.DIAGNOSING)
        ctrl._set_state(AppState.DIAGNOSING)  # 重复，不应触发

        assert states == [AppState.DIAGNOSING]

    def test_open_manifest_dir_goes_to_diagnosing(self, tmp_path):
        """打开清单目录 → DIAGNOSING。"""
        # 创建假清单
        manifest = tmp_path / "timeline_manifest.json"
        manifest.write_text(
            '{"version": "1.0-mini", "run_id": "test", "goal": "", '
            '"source_media": {"path": "v.mp4"}, "annotations": [], '
            '"current": {}, "layer_status": {}}',
            encoding="utf-8",
        )

        ctrl = self._make_ctrl()
        states: list[AppState] = []
        ctrl.on_state_change(states.append)

        resolved = ctrl.open(tmp_path)

        assert ctrl.state == AppState.DIAGNOSING
        assert AppState.DIAGNOSING in states
        assert ctrl.progress_report is not None
        assert resolved.manifest_path == manifest

    def test_open_nonexistent_raises(self):
        ctrl = self._make_ctrl()
        with pytest.raises(FileNotFoundError):
            ctrl.open(Path("/nonexistent/path"))

    def test_confirm_resume_goes_to_ready(self, tmp_path):
        """confirm_resume() → READY。"""
        manifest = tmp_path / "timeline_manifest.json"
        manifest.write_text(
            '{"version": "1.0-mini", "run_id": "test", "goal": "test goal", '
            '"source_media": {"path": "v.mp4"}, "annotations": [], '
            '"current": {}, "layer_status": {}}',
            encoding="utf-8",
        )

        ctrl = self._make_ctrl()
        ctrl.open(tmp_path)
        assert ctrl.state == AppState.DIAGNOSING

        mock_run = MagicMock()
        mock_session = MagicMock()
        mock_cfg = MagicMock()

        with patch("autosmartcut.session_factory.build_session") as mock_build:
            mock_build.return_value = (mock_run, mock_session, mock_cfg)
            ctrl.confirm_resume(stage="3", goal="test goal")

        assert ctrl.state == AppState.READY
        assert ctrl.is_ready is True

    def test_confirm_resume_without_open_raises(self):
        ctrl = self._make_ctrl()
        with pytest.raises(RuntimeError, match="open"):
            ctrl.confirm_resume(stage="3", goal="test")

    def test_on_pipeline_event_callback(self):
        """pipeline 事件转发给 UI 层。"""
        ctrl = self._make_ctrl()
        events = []
        ctrl.on_pipeline_event(events.append)

        mock_event = MagicMock()
        mock_event.type = "stage_enter"
        ctrl._set_state(AppState.READY)  # 先设为 READY
        ctrl._on_session_event(mock_event)

        assert mock_event in events

    def test_session_event_drives_state_transitions(self):
        """session 事件驱动状态转换。"""
        ctrl = self._make_ctrl()
        ctrl._set_state(AppState.READY)

        # stage_enter → RUNNING
        event = MagicMock()
        event.type = "stage_enter"
        ctrl._on_session_event(event)
        assert ctrl.state == AppState.RUNNING

        # pipeline_complete → COMPLETED
        event2 = MagicMock()
        event2.type = "pipeline_complete"
        ctrl._on_session_event(event2)
        assert ctrl.state == AppState.COMPLETED

    def test_session_event_paused(self):
        ctrl = self._make_ctrl()
        ctrl._set_state(AppState.RUNNING)

        event = MagicMock()
        event.type = "paused"
        ctrl._on_session_event(event)
        assert ctrl.state == AppState.PAUSED

    def test_session_event_error(self):
        ctrl = self._make_ctrl()
        ctrl._set_state(AppState.RUNNING)

        event = MagicMock()
        event.type = "error"
        ctrl._on_session_event(event)
        assert ctrl.state == AppState.FAILED
