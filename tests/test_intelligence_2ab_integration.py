import sys
import types

import pytest

sys.modules.setdefault("av", types.ModuleType("av"))

from autosmartcut.intelligence_2a import run_2a_comprehension
from autosmartcut.intelligence_2b import run_2b_decision


def test_run_2a_and_2b_with_mocked_llm(monkeypatch):
    manifest = {
        "source": "demo.mp4",
        "goal": "提取核心观点",
        "annotations": [
            {"index": 0, "content": "今天我们讨论自动剪辑流程。"},
            {"index": 1, "content": "第一步是转写，第二步是语义理解。"},
            {"index": 2, "content": "最后根据目标决定保留片段。"},
        ],
    }

    responses = iter(
        [
            {
                "purpose_rough": "介绍自动剪辑流程。",
                "outline_blocks_rough": [{"start_index": 0, "end_index": 2, "topic": "流程"}],
                "candidate_misrecognitions": [],
            },
            {
                "purpose": "讲解自动剪辑的核心步骤与决策思路。",
                "outline_blocks": [{"start_index": 0, "end_index": 2, "summary": "自动剪辑流程总览"}],
                "corrections": [],
            },
            {
                "decisions": [
                    {"index": 0, "keep": True},
                    {"index": 1, "keep": True},
                    {"index": 2, "keep": False},
                ]
            },
        ]
    )

    def _fake_call_llm_structured(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr("autosmartcut.intelligence_2a.call_llm_structured", _fake_call_llm_structured)
    monkeypatch.setattr("autosmartcut.intelligence_2b.call_llm_structured", _fake_call_llm_structured)

    out_2a = run_2a_comprehension(manifest)
    assert out_2a["comprehension"]["purpose"] == "讲解自动剪辑的核心步骤与决策思路。"
    assert len(out_2a["comprehension"]["outline_blocks"]) == 1
    assert len(out_2a["comprehension"]["cleaned_annotations"]) == len(manifest["annotations"])
    for i, item in enumerate(out_2a["comprehension"]["cleaned_annotations"]):
        assert item["annotation_index"] == manifest["annotations"][i]["index"]
        assert item["cleaned_content"] == manifest["annotations"][i]["content"]

    out_2b = run_2b_decision(out_2a)
    assert len(out_2b["keep_mask"]) == len(manifest["annotations"])
    assert out_2b["keep_mask"][0] == {"index": 0, "keep": True}
    assert out_2b["keep_mask"][2] == {"index": 2, "keep": False}


def test_run_2a_outputs_dense_cleaned_annotations_with_corrections(monkeypatch):
    manifest = {
        "source": "demo.mp4",
        "goal": "提取核心观点",
        "annotations": [
            {"index": 0, "content": "今天我们讨论自动减辑流程。"},
            {"index": 1, "content": "第一步是转写，第二步是语义理解。"},
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
    assert len(cleaned) == len(manifest["annotations"])
    assert cleaned[0] == {"annotation_index": 0, "cleaned_content": "今天我们讨论自动剪辑流程。"}
    assert cleaned[1] == {
        "annotation_index": 1,
        "cleaned_content": "第一步是转写，第二步是语义理解。",
    }


def test_run_2b_rejects_non_dense_cleaned_annotations():
    manifest = {
        "goal": "提取核心观点",
        "annotations": [
            {"index": 0, "content": "A"},
            {"index": 1, "content": "B"},
        ],
        "comprehension": {
            "purpose": "测试",
            "outline_blocks": [],
            # 非稠密：缺失 index=1
            "cleaned_annotations": [{"annotation_index": 0, "cleaned_content": "A"}],
        },
    }

    with pytest.raises(ValueError, match="稠密全量序列"):
        run_2b_decision(manifest)


def test_run_2b_rejects_misaligned_cleaned_annotations_index():
    manifest = {
        "goal": "提取核心观点",
        "annotations": [
            {"index": 0, "content": "A"},
            {"index": 1, "content": "B"},
        ],
        "comprehension": {
            "purpose": "测试",
            "outline_blocks": [],
            # 长度正确但索引错位
            "cleaned_annotations": [
                {"annotation_index": 0, "cleaned_content": "A"},
                {"annotation_index": 2, "cleaned_content": "B"},
            ],
        },
    }

    with pytest.raises(ValueError, match="未对齐"):
        run_2b_decision(manifest)


def test_run_2b_rejects_non_string_cleaned_content():
    manifest = {
        "goal": "提取核心观点",
        "annotations": [
            {"index": 0, "content": "A"},
        ],
        "comprehension": {
            "purpose": "测试",
            "outline_blocks": [],
            "cleaned_annotations": [
                {"annotation_index": 0, "cleaned_content": None},
            ],
        },
    }

    with pytest.raises(ValueError, match="必须为字符串"):
        run_2b_decision(manifest)
