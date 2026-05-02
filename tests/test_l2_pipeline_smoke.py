"""Layer 2 窄端到端冒烟：2a → 2b（mock LLM）。导入链含 perception → torch。"""

import json
import sys
import types

import pytest

sys.modules.setdefault("av", types.ModuleType("av"))

from autosmartcut.nodes.l2.intelligence_2a import run_2a_comprehension
from autosmartcut.nodes.l2.intelligence_2b import run_2b_decision
from autosmartcut.nodes.l2.intelligence_llm import StructuredResult


def test_run_2a_and_2b_with_mocked_llm(monkeypatch):
    manifest = {
        "source": "demo.mp4",
        "goal": "提取核心观点",
        "tokens": [
            {"index": 0, "text": "今天我们讨论自动剪辑流程。"},
            {"index": 1, "text": "第一步是转写，第二步是语义理解。"},
            {"index": 2, "text": "最后根据目标决定保留片段。"},
        ],
    }

    r1_data = {
        "purpose_rough": "介绍自动剪辑流程。",
        "outline_blocks_rough": [{"start_index": 0, "end_index": 2, "topic": "流程"}],
        "candidate_misrecognitions": [],
    }
    r2_data = {
        "purpose": "讲解自动剪辑的核心步骤与决策思路。",
        "outline_blocks": [{"start_index": 0, "end_index": 2, "summary": "自动剪辑流程总览"}],
        "corrections": [],
    }
    decision_data = {
        "decisions": [
            {"index": 0, "keep": True},
            {"index": 1, "keep": True},
            {"index": 2, "keep": False},
        ]
    }
    post_r1 = iter([r2_data])

    def _fake_call_structured_2a(messages, schema, stage, **kwargs):
        if stage == "r1":
            return StructuredResult(
                data=r1_data,
                assistant_content=json.dumps(r1_data, ensure_ascii=False),
                usage={},
                request_messages=[
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "u1"},
                ],
            )
        if stage == "r2":
            return StructuredResult(
                data=next(post_r1),
                assistant_content="{}",
                usage={},
                request_messages=[],
            )
        raise AssertionError(stage)

    def _fake_call_structured_2b(messages, schema, stage, **kwargs):
        if stage == "decision_r1":
            return StructuredResult(
                data={
                    "decisions": [
                        {"index": 0, "keep": True, "reason": "ok"},
                        {"index": 1, "keep": True, "reason": "ok"},
                        {"index": 2, "keep": True, "reason": "ok"},
                    ]
                },
                assistant_content="{}",
                usage={},
                request_messages=[],
            )
        if stage == "decision_r2":
            return StructuredResult(
                data=decision_data,
                assistant_content="{}",
                usage={},
                request_messages=[],
            )
        raise AssertionError(stage)

    monkeypatch.setattr(
        "autosmartcut.nodes.l2.intelligence_2a.call_structured",
        _fake_call_structured_2a,
    )
    for _target in (
        "autosmartcut.nodes.l2.intelligence_2b.call_structured",
        "autosmartcut.nodes.l2.intelligence_2b_dispatch.call_structured",
    ):
        monkeypatch.setattr(_target, _fake_call_structured_2b)

    out_2a = run_2a_comprehension(manifest)
    assert out_2a["comprehension"]["purpose"] == "讲解自动剪辑的核心步骤与决策思路。"
    assert len(out_2a["comprehension"]["outline_blocks"]) == 1
    assert len(out_2a["comprehension"]["cleaned_annotations"]) == len(manifest["tokens"])
    for i, item in enumerate(out_2a["comprehension"]["cleaned_annotations"]):
        assert item["annotation_index"] == manifest["tokens"][i]["index"]
        assert item["cleaned_content"] == manifest["tokens"][i]["text"]

    out_2b = run_2b_decision(out_2a)
    assert len(out_2b["keep_mask"]) == len(manifest["tokens"])
    assert out_2b["keep_mask"][0] == {"index": 0, "keep": True}
    assert out_2b["keep_mask"][2] == {"index": 2, "keep": False}
