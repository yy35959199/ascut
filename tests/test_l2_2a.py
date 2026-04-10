"""Layer 2 / 2a 理解子阶段单测。导入链含 perception → torch。"""

import sys
import types

import pytest

sys.modules.setdefault("av", types.ModuleType("av"))

from autosmartcut.intelligence_2a import run_2a_comprehension


def test_run_2a_outputs_dense_cleaned_annotations_with_corrections(monkeypatch):
    manifest = {
        "source": "demo.mp4",
        "goal": "提取核心观点",
        "tokens": [
            {"index": 0, "text": "今天我们讨论自动减辑流程。"},
            {"index": 1, "text": "第一步是转写，第二步是语义理解。"},
        ],
    }

    responses = iter(
        [
            {
                "purpose_rough": "介绍自动剪辑流程。",
                "outline_blocks_rough": [{"start_index": 0, "end_index": 1, "topic": "流程"}],
                "candidate_misrecognitions": [],
            },
            {
                "purpose": "讲解自动剪辑流程。",
                "outline_blocks": [{"start_index": 0, "end_index": 1, "summary": "流程总览"}],
                "corrections": [{"index": 0, "old": "减辑", "nth": 1, "new": "剪辑"}],
            },
        ]
    )

    def _fake_call_llm_structured(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr("autosmartcut.intelligence_2a.call_llm_structured", _fake_call_llm_structured)

    out_2a = run_2a_comprehension(manifest)
    cleaned = out_2a["comprehension"]["cleaned_annotations"]
    assert len(cleaned) == len(manifest["tokens"])
    assert cleaned[0] == {"annotation_index": 0, "cleaned_content": "今天我们讨论自动剪辑流程。"}
    assert cleaned[1] == {
        "annotation_index": 1,
        "cleaned_content": "第一步是转写，第二步是语义理解。",
    }
