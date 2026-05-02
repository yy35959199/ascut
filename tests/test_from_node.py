"""test_from_node.py — from_node 子阶段重跑功能专项测试。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from autosmartcut.manifest.manifest_progress import infer_progress
from autosmartcut.pipeline.session_factory import (
    PipelineParams,
    _FROM_NODE_MAP,
    _L2_TOPO_ORDER,
    _validate_from_node_prereqs,
)


# ---------------------------------------------------------------------------
# 辅助：构造测试用清单数据
# ---------------------------------------------------------------------------

def _make_manifest(
    l1_done: bool = False,
    l2a_done: bool = False,
    l2b_done: bool = False,
    l2c_done: bool = False,
    l2d_done: bool = False,
) -> dict:
    """构造测试用清单 dict。"""
    layer_status = {}
    if l1_done:
        layer_status["l1_perception"] = {
            "started_at": "2026-01-01T00:00:00Z",
            "completed_at": "2026-01-01T00:01:00Z",
        }
    if l2a_done:
        layer_status["l2a_comprehension"] = {
            "started_at": "2026-01-01T00:01:00Z",
            "completed_at": "2026-01-01T00:02:00Z",
        }
    if l2b_done:
        layer_status["l2b_decision"] = {
            "started_at": "2026-01-01T00:02:00Z",
            "completed_at": "2026-01-01T00:03:00Z",
        }
    if l2c_done:
        layer_status["l2c_review"] = {
            "started_at": "2026-01-01T00:03:00Z",
            "completed_at": "2026-01-01T00:04:00Z",
        }
    if l2d_done:
        layer_status["l2d_human"] = {
            "started_at": "2026-01-01T00:04:00Z",
            "completed_at": "2026-01-01T00:05:00Z",
        }

    current = {}
    if l2a_done:
        current["comprehension"] = {"purpose": "测试目的", "outline_blocks": [{"title": "A"}]}
    if l2b_done:
        current["keep_mask"] = [{"index": 0, "keep": True}]
    if l2c_done:
        current["review_report"] = {"verdict": "pass", "checklist": []}
    if l2d_done:
        current["human_feedback_history"] = []
        current["l2d_completed"] = True

    return {
        "version": "1.0-mini",
        "run_id": "test-001",
        "goal": "测试",
        "source_media": {"path": "/fake/video.mp4"},
        "annotations": [
            {"index": 0, "content": "hello", "t_start": 0.0, "t_end": 1.0}
        ] if l1_done else [],
        "current": current,
        "layer_status": layer_status,
    }


# ---------------------------------------------------------------------------
# Step 1: manifest_progress.resumable_from
# ---------------------------------------------------------------------------

class TestResumableFrom:
    def test_no_l1_all_false(self):
        data = _make_manifest()
        report = infer_progress(data, Path("/fake/manifest.json"))
        assert report.resumable_from == {
            "2a": False, "2b": False, "2c": False, "2d": False
        }

    def test_l1_done_only_2a_true(self):
        data = _make_manifest(l1_done=True)
        report = infer_progress(data, Path("/fake/manifest.json"))
        assert report.resumable_from["2a"] is True
        assert report.resumable_from["2b"] is False
        assert report.resumable_from["2c"] is False
        assert report.resumable_from["2d"] is False

    def test_l1_and_2a_done(self):
        data = _make_manifest(l1_done=True, l2a_done=True)
        report = infer_progress(data, Path("/fake/manifest.json"))
        assert report.resumable_from["2a"] is True
        assert report.resumable_from["2b"] is True
        assert report.resumable_from["2c"] is False
        assert report.resumable_from["2d"] is False

    def test_l1_2a_2b_done(self):
        data = _make_manifest(l1_done=True, l2a_done=True, l2b_done=True)
        report = infer_progress(data, Path("/fake/manifest.json"))
        assert report.resumable_from["2a"] is True
        assert report.resumable_from["2b"] is True
        assert report.resumable_from["2c"] is True
        assert report.resumable_from["2d"] is False

    def test_all_l2_done(self):
        data = _make_manifest(
            l1_done=True, l2a_done=True, l2b_done=True,
            l2c_done=True, l2d_done=True
        )
        report = infer_progress(data, Path("/fake/manifest.json"))
        assert all(report.resumable_from[k] is True for k in ["2a", "2b", "2c", "2d"])


# ---------------------------------------------------------------------------
# Step 2: PipelineSession._apply_from_node_skip（通过 NodeState 验证）
# ---------------------------------------------------------------------------

class TestApplyFromNodeSkip:
    def _make_session(self, from_node: str | None):
        """构造一个最小化的 PipelineSession 用于测试 _apply_from_node_skip。"""
        from unittest.mock import MagicMock
        from autosmartcut.pipeline.pipeline_session import PipelineSession
        from autosmartcut.pipeline.pipeline_models import NodeState

        cfg = MagicMock()
        session = PipelineSession.__new__(PipelineSession)
        session._from_node = from_node
        session._stage_filter = frozenset({2})

        # 注册 L2 节点的最小 mock
        nodes = {}
        node_states = {}
        for nid in PipelineSession._L2_TOPO_ORDER:
            node = MagicMock()
            node.id = nid
            node.writes = frozenset()
            nodes[nid] = node
            node_states[nid] = NodeState(node_id=nid, status="pending")

        session._nodes = nodes
        session._node_states = node_states
        return session

    def test_from_node_none_no_skip(self):
        session = self._make_session(None)
        session._apply_from_node_skip({})
        for nid in session._L2_TOPO_ORDER:
            assert session._node_states[nid].status == "pending"

    def test_from_node_2a_no_skip(self):
        """from_node=2a 不跳过任何节点（从头开始）。"""
        session = self._make_session("2a")
        session._apply_from_node_skip({})
        for nid in session._L2_TOPO_ORDER:
            assert session._node_states[nid].status == "pending"

    def test_from_node_2b_skips_2a(self):
        session = self._make_session("2b")
        session._apply_from_node_skip({"current": {}})
        assert session._node_states["l2a_comprehension"].status == "skipped"
        assert session._node_states["l2b_decision"].status == "pending"
        assert session._node_states["l2c_review"].status == "pending"
        assert session._node_states["l2d_human"].status == "pending"

    def test_from_node_2c_skips_2a_2b(self):
        session = self._make_session("2c")
        session._apply_from_node_skip({"current": {}})
        assert session._node_states["l2a_comprehension"].status == "skipped"
        assert session._node_states["l2b_decision"].status == "skipped"
        assert session._node_states["l2c_review"].status == "pending"
        assert session._node_states["l2d_human"].status == "pending"

    def test_from_node_2d_skips_2a_2b_2c(self):
        session = self._make_session("2d")
        session._apply_from_node_skip({"current": {}})
        assert session._node_states["l2a_comprehension"].status == "skipped"
        assert session._node_states["l2b_decision"].status == "skipped"
        assert session._node_states["l2c_review"].status == "skipped"
        assert session._node_states["l2d_human"].status == "pending"

    def test_from_node_2b_backfills_comprehension(self):
        """from_node=2b 时，2a 的产物 comprehension 应从 current 回填到顶层。"""
        from unittest.mock import MagicMock
        from autosmartcut.pipeline.pipeline_session import PipelineSession
        from autosmartcut.pipeline.pipeline_models import NodeState

        session = self._make_session("2b")
        # 给 l2a_comprehension 节点设置 writes
        session._nodes["l2a_comprehension"].writes = frozenset({"comprehension"})

        manifest = {"current": {"comprehension": {"purpose": "test"}}}
        session._apply_from_node_skip(manifest)

        assert "comprehension" in manifest
        assert manifest["comprehension"]["purpose"] == "test"


# ---------------------------------------------------------------------------
# Step 3: _validate_from_node_prereqs
# ---------------------------------------------------------------------------

class TestValidateFromNodePrereqs:
    def _write_manifest(self, tmp_path: Path, data: dict) -> Path:
        mp = tmp_path / "timeline_manifest.json"
        mp.write_text(json.dumps(data), encoding="utf-8")
        return mp

    def test_invalid_from_node_raises(self, tmp_path):
        mp = self._write_manifest(tmp_path, _make_manifest(l1_done=True))
        with pytest.raises(ValueError, match="非法 --from-node"):
            _validate_from_node_prereqs("99", frozenset({2}), mp)

    def test_from_node_without_stage2_raises(self, tmp_path):
        mp = self._write_manifest(tmp_path, _make_manifest(l1_done=True))
        with pytest.raises(ValueError, match="需要 --stage 包含阶段 2"):
            _validate_from_node_prereqs("2b", frozenset({3}), mp)

    def test_from_node_2a_always_passes(self, tmp_path):
        """from_node=2a 不需要任何前置节点完成。"""
        mp = self._write_manifest(tmp_path, _make_manifest())
        # 不应抛异常
        _validate_from_node_prereqs("2a", frozenset({2}), mp)

    def test_from_node_2b_requires_2a_completed(self, tmp_path):
        """from_node=2b 要求 2a 已完成。"""
        mp = self._write_manifest(tmp_path, _make_manifest(l1_done=True))
        with pytest.raises(ValueError, match="l2a_comprehension.*已完成"):
            _validate_from_node_prereqs("2b", frozenset({2}), mp)

    def test_from_node_2b_passes_when_2a_done(self, tmp_path):
        mp = self._write_manifest(
            tmp_path,
            _make_manifest(l1_done=True, l2a_done=True)
        )
        # 不应抛异常
        _validate_from_node_prereqs("2b", frozenset({2}), mp)

    def test_from_node_2c_requires_2a_2b_completed(self, tmp_path):
        mp = self._write_manifest(
            tmp_path,
            _make_manifest(l1_done=True, l2a_done=True)  # 2b 未完成
        )
        with pytest.raises(ValueError, match="l2b_decision.*已完成"):
            _validate_from_node_prereqs("2c", frozenset({2}), mp)

    def test_from_node_2c_passes_when_2a_2b_done(self, tmp_path):
        mp = self._write_manifest(
            tmp_path,
            _make_manifest(l1_done=True, l2a_done=True, l2b_done=True)
        )
        _validate_from_node_prereqs("2c", frozenset({2}), mp)

    def test_missing_current_field_raises(self, tmp_path):
        """2a 完成但 current.comprehension 缺失时应报错。"""
        data = _make_manifest(l1_done=True, l2a_done=True)
        # 删除 current.comprehension
        data["current"].pop("comprehension", None)
        mp = self._write_manifest(tmp_path, data)
        with pytest.raises(ValueError, match="comprehension"):
            _validate_from_node_prereqs("2b", frozenset({2}), mp)


# ---------------------------------------------------------------------------
# Step 5: CLI --from-node 参数解析
# ---------------------------------------------------------------------------

class TestCLIFromNode:
    def test_from_node_parsed_correctly(self, tmp_path):
        from autosmartcut.cli.runner import _parse_args
        # 创建真实的临时清单文件
        mp = tmp_path / "timeline_manifest.json"
        mp.write_text(json.dumps(_make_manifest(l1_done=True)), encoding="utf-8")
        args = _parse_args([
            "run",
            "--manifest", str(mp),
            "--stage", "2",
            "--from-node", "2b",
        ])
        assert getattr(args, "from_node", None) == "2b"

    def test_from_node_invalid_choice_raises(self, tmp_path):
        from autosmartcut.cli.runner import _parse_args
        mp = tmp_path / "timeline_manifest.json"
        mp.write_text(json.dumps(_make_manifest(l1_done=True)), encoding="utf-8")
        with pytest.raises(SystemExit):
            _parse_args([
                "run",
                "--manifest", str(mp),
                "--stage", "2",
                "--from-node", "99",
            ])

    def test_from_node_without_stage2_raises(self, tmp_path):
        """--from-node 搭配不含 L2 的 --stage 应报错。"""
        from autosmartcut.cli.runner import _parse_args
        mp = tmp_path / "timeline_manifest.json"
        mp.write_text(json.dumps(_make_manifest(l1_done=True, l2a_done=True, l2b_done=True, l2c_done=True, l2d_done=True)), encoding="utf-8")
        with pytest.raises(SystemExit):
            _parse_args([
                "run",
                "--manifest", str(mp),
                "--stage", "3",
                "--from-node", "2b",
            ])

    def test_from_node_none_by_default(self, tmp_path):
        from autosmartcut.cli.runner import _parse_args
        mp = tmp_path / "timeline_manifest.json"
        mp.write_text(json.dumps(_make_manifest(l1_done=True)), encoding="utf-8")
        args = _parse_args([
            "run",
            "--manifest", str(mp),
            "--stage", "2",
        ])
        assert getattr(args, "from_node", None) is None
