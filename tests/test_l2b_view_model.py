"""l2b_view_model：增量 JSON 解析与 TwoBViewModel 状态。"""

from __future__ import annotations

from autosmartcut.nodes.l2.intelligence_llm import StreamChunk
from autosmartcut.nodes.l2.l2b_view_model import BlockDecisionParser, TwoBViewModel


def test_block_decision_parser_incremental_and_truncated() -> None:
    p = BlockDecisionParser(has_reason=True)
    assert p.feed('{"index": 0, "keep": true, "reason": "ok"}') == [
        {"index": 0, "keep": True, "reason": "ok"},
    ]
    p2 = BlockDecisionParser(has_reason=True)
    part1 = '{"index": 1, "keep": false, "reason": "filler'
    assert p2.feed(part1) == []
    part2 = '"}'
    assert p2.feed(part2) == [{"index": 1, "keep": False, "reason": "filler"}]


def test_two_b_view_model_r1_r2_phases() -> None:
    vm = TwoBViewModel(show_thinking_default=True)
    c1 = StreamChunk(
        stage="decision_r1",
        event="content_delta",
        content_delta='{"index":0,"keep":true,"reason":"ok"}',
    )
    vm.update_chunk(1, c1)
    st = vm.get_block_state(1)
    assert st is not None
    assert st.stage == "decision_r1"
    assert st.decisions
    c2 = StreamChunk(
        stage="decision_r2",
        event="result",
    )
    vm.update_chunk(3, c2)
    assert vm.get_phase() in ("r2", "done")
