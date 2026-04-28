"""pipeline_session DAG 构建与调度核心单测。

覆盖：
- DAG 依赖推导正确性（6 节点标准拓扑，L1→L2→L3 线性）
- 环路检测
- 并行批次推导（多节点同时可调度时 RUN_BATCH）
- stage_filter / resumable skip
- REFLOW 重置逻辑
- parse_stage_arg 映射表
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autosmartcut.pipeline.pipeline_models import (
    NodeState,
    PipelineSnapshot,
    SchedulerAction,
    SchedulerActionType,
    StageContext,
    StageResult,
    StageStatus,
)
from autosmartcut.pipeline.pipeline_protocols import CyclicDependencyError
from autosmartcut.pipeline.pipeline_session import PipelineSession


# ---------------------------------------------------------------------------
# 辅助：重建标准 7 节点 DAG 拓扑（不依赖真实节点实现，避免 qwen_asr 导入）
# ---------------------------------------------------------------------------

def _register_standard_dag_stubs(session: PipelineSession) -> None:
    """用 stub 节点重建标准 6 节点 DAG 拓扑，与真实节点的 reads/writes 完全一致。"""
    nodes = [
        StubNode("l1_perception",
                 reads=frozenset({"source_media"}),
                 writes=frozenset({"annotations", "raw_text"}),
                 phase=1, resumable=False),
        StubNode("l2a_comprehension",
                 reads=frozenset({"annotations", "goal"}),
                 writes=frozenset({"comprehension"}),
                 phase=2, resumable=True),
        StubNode("l2b_decision",
                 reads=frozenset({"comprehension", "annotations"}),
                 writes=frozenset({"keep_mask"}),
                 phase=2, resumable=True),
        StubNode("l2c_review",
                 reads=frozenset({"keep_mask", "comprehension", "annotations", "goal"}),
                 writes=frozenset({"review_report"}),
                 phase=2, resumable=True),
        StubNode("l2d_human",
                 reads=frozenset({"keep_mask", "review_report", "comprehension"}),
                 writes=frozenset({"human_feedback_history", "l2d_completed"}),
                 phase=2, resumable=True),
        StubNode("l3_execute",
                 reads=frozenset({"annotations", "keep_mask", "source_media", "l2d_completed"}),
                 writes=frozenset({"output_video"}),
                 phase=3, resumable=True),
    ]
    for n in nodes:
        session.register(n)

@dataclass
class StubNode:
    id: str
    reads: frozenset
    writes: frozenset
    phase: int = 1
    resumable: bool = True
    _result: StageResult = field(default_factory=lambda: StageResult(status=StageStatus.SUCCESS, summary="ok"))

    async def run(self, ctx: StageContext) -> StageResult:
        return self._result

    def summarize(self, manifest: dict) -> Any:
        return None


def _make_session(tmp_path: Path, nodes: list[StubNode] | None = None) -> PipelineSession:
    """构造一个带最小 manifest 的 PipelineSession，注入 stub 节点。"""
    from autosmartcut.manifest.manifest_io import make_manifest_skeleton, save_manifest

    mp = tmp_path / "timeline_manifest.json"
    sk = make_manifest_skeleton("test-run", "goal", str(tmp_path / "v.mp4"))
    save_manifest(mp, sk, atomic=True)

    cfg = MagicMock()
    cfg.intelligence.two_c_max_review_rounds = 1
    cfg.intelligence.two_b_mode = "single"

    session = PipelineSession(mp, cfg)
    if nodes:
        for n in nodes:
            session.register(n)
    return session


# ---------------------------------------------------------------------------
# 1. DAG 构建：6 节点标准拓扑
# ---------------------------------------------------------------------------

class TestBuildDag:
    def test_standard_6_nodes_no_error(self, tmp_path: Path) -> None:
        """注册标准 6 节点后 _build_dag() 不抛异常。"""
        session = _make_session(tmp_path)
        _register_standard_dag_stubs(session)
        session._build_dag()  # 不应抛异常

    def test_l3_execute_depends_on_l2d_human(self, tmp_path: Path) -> None:
        """l3_execute 必须依赖 l2d_human（通过 l2d_completed 字段）。"""
        session = _make_session(tmp_path)
        _register_standard_dag_stubs(session)
        session._build_dag()
        assert "l2d_human" in session._dag["l3_execute"]

    def test_cyclic_dependency_raises(self, tmp_path: Path) -> None:
        """含环路的节点集合应抛出 CyclicDependencyError。"""
        session = _make_session(tmp_path)
        # A 写 x，B 读 x 写 y，A 读 y → 环路
        a = StubNode("node_a", reads=frozenset({"y"}), writes=frozenset({"x"}))
        b = StubNode("node_b", reads=frozenset({"x"}), writes=frozenset({"y"}))
        session.register(a)
        session.register(b)
        with pytest.raises(CyclicDependencyError):
            session._build_dag()

    def test_duplicate_writes_raises(self, tmp_path: Path) -> None:
        """两个节点写同一字段应抛出 ValueError。"""
        session = _make_session(tmp_path)
        a = StubNode("node_a", reads=frozenset(), writes=frozenset({"shared_field"}))
        b = StubNode("node_b", reads=frozenset(), writes=frozenset({"shared_field"}))
        session.register(a)
        session.register(b)
        with pytest.raises(ValueError, match="被多个节点写出"):
            session._build_dag()


# ---------------------------------------------------------------------------
# 2. 并行批次推导
# ---------------------------------------------------------------------------

class TestSchedulable:
    def test_l2a_only_schedulable_after_l1(self, tmp_path: Path) -> None:
        """l1_perception 完成后，仅 l2a_comprehension 可调度（无 L3 预计算并行）。"""
        session = _make_session(tmp_path)
        _register_standard_dag_stubs(session)
        session._build_dag()

        session._node_states["l1_perception"].status = "completed"

        manifest = {"source_media": {}, "annotations": [], "goal": ""}
        snapshot = session._build_snapshot(manifest)

        assert snapshot.schedulable_nodes == ["l2a_comprehension"]

    def test_nothing_schedulable_when_all_pending_with_deps(self, tmp_path: Path) -> None:
        """所有节点 pending 且有依赖时，只有无依赖节点可调度。"""
        session = _make_session(tmp_path)
        _register_standard_dag_stubs(session)
        session._build_dag()

        manifest = {}
        snapshot = session._build_snapshot(manifest)
        assert snapshot.schedulable_nodes == ["l1_perception"]


# ---------------------------------------------------------------------------
# 3. stage_filter 与 resumable skip
# ---------------------------------------------------------------------------

class TestStageFilter:
    def test_parse_stage_arg_123(self) -> None:
        sf, nf = PipelineSession.parse_stage_arg("123")
        assert sf == frozenset({1, 2, 3})
        assert nf is None

    def test_parse_stage_arg_invalid(self) -> None:
        with pytest.raises(ValueError):
            PipelineSession.parse_stage_arg("99")

    def test_apply_stage_filter_skips_phase3(self, tmp_path: Path) -> None:
        """stage_filter={1,2} 时，phase=3 的节点应被标记为 skipped。"""
        session = _make_session(tmp_path)
        session._stage_filter = frozenset({1, 2})
        n1 = StubNode("n1", reads=frozenset(), writes=frozenset({"a"}), phase=1)
        n2 = StubNode("n2", reads=frozenset({"a"}), writes=frozenset({"b"}), phase=3)
        session.register(n1)
        session.register(n2)
        session._build_dag()
        session._apply_stage_filter({})
        assert session._node_states["n2"].status == "skipped"
        assert session._node_states["n1"].status == "pending"

    def test_resumable_skip_when_layer_status_present(self, tmp_path: Path) -> None:
        """resumable=True 且 layer_status 有完成标记时，节点应被跳过。"""
        session = _make_session(tmp_path)
        n = StubNode("n1", reads=frozenset(), writes=frozenset({"a"}), resumable=True)
        session.register(n)
        session._build_dag()
        # 使用新格式：layer_status[node_id] = {completed_at: "..."}
        manifest = {"layer_status": {"n1": {"completed_at": "2026-01-01T00:00:00"}}}
        session._apply_resumable_skip(manifest)
        assert session._node_states["n1"].status == "skipped"

    def test_non_resumable_not_skipped(self, tmp_path: Path) -> None:
        """resumable=False 的节点即使有完成标记也不跳过。"""
        session = _make_session(tmp_path)
        n = StubNode("n1", reads=frozenset(), writes=frozenset({"a"}), resumable=False)
        session.register(n)
        session._build_dag()
        manifest = {"layer_status": {"n1_completed_at": "2026-01-01T00:00:00"}}
        session._apply_resumable_skip(manifest)
        assert session._node_states["n1"].status == "pending"


# ---------------------------------------------------------------------------
# 4. REFLOW 重置逻辑
# ---------------------------------------------------------------------------

class TestHandleReflow:
    def _make_session_with_dag(self, tmp_path: Path) -> tuple[PipelineSession, dict]:
        from autosmartcut.manifest.manifest_io import make_manifest_skeleton, save_manifest

        mp = tmp_path / "timeline_manifest.json"
        sk = make_manifest_skeleton("r", "", str(tmp_path / "v.mp4"))
        save_manifest(mp, sk, atomic=True)
        cfg = MagicMock()
        cfg.intelligence.two_c_max_review_rounds = 1
        cfg.intelligence.two_b_mode = "single"

        session = PipelineSession(mp, cfg)
        _register_standard_dag_stubs(session)
        session._build_dag()
        # 模拟所有节点已完成
        for nid in session._node_states:
            session._node_states[nid].status = "completed"
        manifest = {
            "layer_status": {f"{nid}_completed_at": "2026-01-01" for nid in session._nodes},
            "comprehension": {},
            "keep_mask": [],
            "review_report": {},
            "human_feedback_history": [],
            "l2d_completed": True,
        }
        return session, manifest

    def test_reflow_2b_resets_l2b_l2c_l2d(self, tmp_path: Path) -> None:
        """REFLOW_2B 应重置 l2b_decision、l2c_review、l2d_human。"""
        session, manifest = self._make_session_with_dag(tmp_path)
        asyncio.run(session._handle_reflow("l2b_decision", manifest))
        assert session._node_states["l2b_decision"].status == "pending"
        assert session._node_states["l2c_review"].status == "pending"
        assert session._node_states["l2d_human"].status == "pending"
        # l2a 不应被重置
        assert session._node_states["l2a_comprehension"].status == "completed"

    def test_reflow_2a_resets_l2a_and_downstream(self, tmp_path: Path) -> None:
        """REFLOW_2A 应重置 l2a_comprehension 及其所有下游。"""
        session, manifest = self._make_session_with_dag(tmp_path)
        asyncio.run(session._handle_reflow("l2a_comprehension", manifest))
        for nid in ("l2a_comprehension", "l2b_decision", "l2c_review", "l2d_human"):
            assert session._node_states[nid].status == "pending", f"{nid} 应为 pending"

    def test_reflow_clears_manifest_fields(self, tmp_path: Path) -> None:
        """回流应清除被重置节点的 writes 字段。"""
        session, manifest = self._make_session_with_dag(tmp_path)
        manifest["keep_mask"] = [{"index": 0, "keep": True}]
        manifest["review_report"] = {"verdict": "pass"}
        asyncio.run(session._handle_reflow("l2b_decision", manifest))
        # l2b writes keep_mask，应被清除
        assert "keep_mask" not in manifest
        # l2c writes review_report，应被清除
        assert "review_report" not in manifest

    def test_reflow_respects_max_reflows(self, tmp_path: Path) -> None:
        """超过 max_reflows 上限时不执行回流。"""
        session, manifest = self._make_session_with_dag(tmp_path)
        session._max_reflows = 1
        session._reflow_count = 1  # 已达上限
        asyncio.run(session._handle_reflow("l2b_decision", manifest))
        # 节点状态不应改变
        assert session._node_states["l2b_decision"].status == "completed"

    def test_reflow_increments_count(self, tmp_path: Path) -> None:
        """每次回流应递增 _reflow_count。"""
        session, manifest = self._make_session_with_dag(tmp_path)
        assert session._reflow_count == 0
        asyncio.run(session._handle_reflow("l2b_decision", manifest))
        assert session._reflow_count == 1

    def test_reflow_clears_launched_set(self, tmp_path: Path) -> None:
        """回流应从 _launched 集合中移除被重置的节点，使调度循环可以重新启动它们。"""
        session, manifest = self._make_session_with_dag(tmp_path)
        # 模拟这些节点已被 start_async 标记为已启动
        session._launched = {"l2b_decision", "l2c_review", "l2d_human", "l2a_comprehension"}
        asyncio.run(session._handle_reflow("l2b_decision", manifest))
        # 被重置的节点应从 _launched 中移除
        assert "l2b_decision" not in session._launched
        assert "l2c_review" not in session._launched
        assert "l2d_human" not in session._launched
        # 未被重置的节点应保留
        assert "l2a_comprehension" in session._launched


# ---------------------------------------------------------------------------
# 5. EventBus
# ---------------------------------------------------------------------------

class TestEventBus:
    def test_subscribe_and_emit(self, tmp_path: Path) -> None:
        session = _make_session(tmp_path)
        received = []
        session.subscribe(received.append)

        from autosmartcut.pipeline.pipeline_events import ProgressEvent
        evt = ProgressEvent(node_id="test", phase="test_phase", payload={"msg": "hello"})
        session._emit(evt)
        assert len(received) == 1
        assert received[0].phase == "test_phase"
        assert received[0].payload == {"msg": "hello"}

    def test_handler_exception_does_not_crash(self, tmp_path: Path) -> None:
        session = _make_session(tmp_path)

        def bad_handler(e):
            raise RuntimeError("handler error")

        session.subscribe(bad_handler)
        from autosmartcut.pipeline.pipeline_events import ProgressEvent
        # 不应抛异常
        session._emit(ProgressEvent(node_id="x", phase="test", payload={}))


# ---------------------------------------------------------------------------
# 6. FixedScheduler
# ---------------------------------------------------------------------------

class TestFixedScheduler:
    def _make_scheduler(self):
        from autosmartcut.pipeline.pipeline_scheduler import FixedScheduler
        cfg = MagicMock()
        cfg.intelligence.two_c_max_review_rounds = 2
        cfg.intelligence.two_b_mode = "single"
        return FixedScheduler(cfg)

    def _make_snapshot(self, schedulable: list[str], **kwargs) -> PipelineSnapshot:
        states = {nid: NodeState(node_id=nid, status="pending") for nid in schedulable}
        return PipelineSnapshot(
            node_states=states,
            manifest_keys=frozenset(),
            schedulable_nodes=schedulable,
            reflow_count=0,
            review_round=kwargs.get("review_round", 0),
            last_review_verdict=kwargs.get("last_review_verdict", ""),
            stage_filter=None,
        )

    def test_complete_when_no_schedulable_and_all_done(self) -> None:
        sched = self._make_scheduler()
        snap = PipelineSnapshot(
            node_states={"n": NodeState(node_id="n", status="completed")},
            manifest_keys=frozenset(),
            schedulable_nodes=[],
            reflow_count=0,
            review_round=0,
            last_review_verdict="",
            stage_filter=None,
        )
        action = asyncio.run(sched.next_action(snap))
        assert action.action_type == SchedulerActionType.COMPLETE

    def test_run_batch_for_multiple_schedulable(self) -> None:
        sched = self._make_scheduler()
        snap = self._make_snapshot(["n2", "n3"])
        action = asyncio.run(sched.next_action(snap))
        assert action.action_type == SchedulerActionType.RUN_BATCH
        assert set(action.node_ids) == {"n2", "n3"}

    def test_run_node_for_single_schedulable(self) -> None:
        sched = self._make_scheduler()
        snap = self._make_snapshot(["l1_perception"])
        action = asyncio.run(sched.next_action(snap))
        assert action.action_type == SchedulerActionType.RUN_NODE
        assert action.node_ids == ["l1_perception"]

    def test_rerun_l2b_on_fix_decision(self) -> None:
        sched = self._make_scheduler()
        snap = self._make_snapshot(
            ["l2b_decision"],
            last_review_verdict="fix_decision",
            review_round=0,
        )
        action = asyncio.run(sched.next_action(snap))
        assert action.action_type == SchedulerActionType.RUN_NODE
        assert action.node_ids == ["l2b_decision"]
        assert action.params.get("review_round") == 0

    def test_force_pass_when_max_rounds_reached(self) -> None:
        sched = self._make_scheduler()
        snap = self._make_snapshot(
            ["l2b_decision"],
            last_review_verdict="fix_decision",
            review_round=2,  # >= max_review_rounds=2
        )
        action = asyncio.run(sched.next_action(snap))
        assert action.params.get("force_pass") is True

    def test_inject_two_b_mode_for_l2b(self) -> None:
        sched = self._make_scheduler()
        snap = self._make_snapshot(["l2b_decision"])
        action = asyncio.run(sched.next_action(snap))
        assert action.params.get("two_b_mode") == "single"


# ---------------------------------------------------------------------------
# 7. 集成：简单 2 节点流水线端到端
# ---------------------------------------------------------------------------

class TestPipelineIntegration:
    def test_two_node_pipeline_completes(self, tmp_path: Path) -> None:
        """两个串行节点的流水线应正常完成并发布 pipeline_complete 事件。"""
        from autosmartcut.manifest.manifest_io import make_manifest_skeleton, save_manifest
        from autosmartcut.pipeline.pipeline_events import PipelineCompleteEvent
        from autosmartcut.pipeline.pipeline_scheduler import FixedScheduler

        mp = tmp_path / "timeline_manifest.json"
        sk = make_manifest_skeleton("r", "", str(tmp_path / "v.mp4"))
        save_manifest(mp, sk, atomic=True)

        cfg = MagicMock()
        cfg.intelligence.two_c_max_review_rounds = 0
        cfg.intelligence.two_b_mode = "single"

        session = PipelineSession(mp, cfg, scheduler=FixedScheduler(cfg))

        n1 = StubNode("n1", reads=frozenset(), writes=frozenset({"field_a"}))
        n2 = StubNode("n2", reads=frozenset({"field_a"}), writes=frozenset({"field_b"}))
        session.register(n1)
        session.register(n2)

        events = []
        session.subscribe(events.append)

        asyncio.run(session.start_async())

        complete_events = [e for e in events if isinstance(e, PipelineCompleteEvent)]
        assert len(complete_events) == 1

    def test_failed_node_aborts_pipeline(self, tmp_path: Path) -> None:
        """节点失败应中止流水线并发布 error 事件。"""
        from autosmartcut.manifest.manifest_io import make_manifest_skeleton, save_manifest
        from autosmartcut.pipeline.pipeline_events import ErrorEvent
        from autosmartcut.pipeline.pipeline_scheduler import FixedScheduler

        mp = tmp_path / "timeline_manifest.json"
        sk = make_manifest_skeleton("r", "", str(tmp_path / "v.mp4"))
        save_manifest(mp, sk, atomic=True)

        cfg = MagicMock()
        cfg.intelligence.two_c_max_review_rounds = 0
        cfg.intelligence.two_b_mode = "single"

        session = PipelineSession(mp, cfg, scheduler=FixedScheduler(cfg))

        fail_result = StageResult(
            status=StageStatus.FAILED,
            summary="intentional failure",
            error=RuntimeError("test error"),
        )
        n1 = StubNode("n1", reads=frozenset(), writes=frozenset({"field_a"}), _result=fail_result)
        session.register(n1)

        events = []
        session.subscribe(events.append)

        asyncio.run(session.start_async())

        error_events = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(error_events) == 1
        assert session._abort_flag is True
