"""Layer 2 / 2b 决策子阶段单测。

仅 import ``intelligence_2b``，不经过 ``intelligence_2a`` / ``perception``。
``perception`` 会加载 ``torch``；2b 本身只有 LLM 调用。无 torch 时可单独跑::

    pytest tests/test_l2_2b.py -q
"""

from __future__ import annotations

import pytest

from autosmartcut.intelligence_2b import run_2b_decision


def test_run_2b_rejects_non_dense_cleaned_annotations():
    manifest = {
        "goal": "提取核心观点",
        "tokens": [
            {"index": 0, "text": "A"},
            {"index": 1, "text": "B"},
        ],
        "comprehension": {
            "purpose": "测试",
            "outline_blocks": [],
            "cleaned_annotations": [{"annotation_index": 0, "cleaned_content": "A"}],
        },
    }

    with pytest.raises(ValueError, match="稠密全量序列"):
        run_2b_decision(manifest)


def test_run_2b_rejects_misaligned_cleaned_annotations_index():
    manifest = {
        "goal": "提取核心观点",
        "tokens": [
            {"index": 0, "text": "A"},
            {"index": 1, "text": "B"},
        ],
        "comprehension": {
            "purpose": "测试",
            "outline_blocks": [],
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
        "tokens": [
            {"index": 0, "text": "A"},
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


def test_run_2b_chunked_two_blocks_merges_keep_mask(monkeypatch):
    manifest = {
        "goal": "提取核心观点",
        "tokens": [
            {"index": 0, "text": "第一句"},
            {"index": 1, "text": "第二句"},
            {"index": 2, "text": "第三句"},
        ],
        "comprehension": {
            "purpose": "测试主旨",
            "outline_blocks": [
                {"start_index": 0, "end_index": 0, "summary": "开场"},
                {"start_index": 1, "end_index": 2, "summary": "正文"},
            ],
            "cleaned_annotations": [
                {"annotation_index": 0, "cleaned_content": "第一句"},
                {"annotation_index": 1, "cleaned_content": "第二句"},
                {"annotation_index": 2, "cleaned_content": "第三句"},
            ],
        },
    }
    responses = iter(
        [
            {"decisions": [{"index": 0, "keep": False}]},
            {"decisions": [{"index": 1, "keep": True}, {"index": 2, "keep": False}]},
        ]
    )

    def _fake_llm(**_kwargs):
        return next(responses)

    monkeypatch.setattr("autosmartcut.intelligence_2b.call_llm_structured", _fake_llm)
    out = run_2b_decision(manifest, mode="block")
    assert out["keep_mask"][0] == {"index": 0, "keep": False}
    assert out["keep_mask"][1] == {"index": 1, "keep": True}
    assert out["keep_mask"][2] == {"index": 2, "keep": False}


def test_run_2b_chunked_empty_outline_falls_back_to_single(monkeypatch):
    manifest = {
        "goal": "g",
        "tokens": [{"index": 0, "text": "仅一句"}],
        "comprehension": {
            "purpose": "p",
            "outline_blocks": [],
            "cleaned_annotations": [{"annotation_index": 0, "cleaned_content": "仅一句"}],
        },
    }
    n_calls = 0

    def _fake_llm(**_kwargs):
        nonlocal n_calls
        n_calls += 1
        return {"decisions": [{"index": 0, "keep": False}]}

    monkeypatch.setattr("autosmartcut.intelligence_2b.call_llm_structured", _fake_llm)
    out = run_2b_decision(manifest, mode="block")
    assert n_calls == 1
    assert out["keep_mask"][0]["keep"] is False


def test_run_2b_chunked_splits_outline_block_by_config_limit(monkeypatch):
    """block 模式下单 outline 块整体一次调用（不再二次拆分）。"""
    manifest = {
        "goal": "g",
        "tokens": [
            {"index": i, "text": f"t{i}"} for i in range(5)
        ],
        "comprehension": {
            "purpose": "p",
            "outline_blocks": [{"start_index": 0, "end_index": 4, "summary": "一整块"}],
            "cleaned_annotations": [
                {"annotation_index": i, "cleaned_content": f"句{i}"} for i in range(5)
            ],
        },
    }
    # block 模式：整块一次调用，返回全部 5 句决策
    responses = iter(
        [
            {"decisions": [
                {"index": 0, "keep": True},
                {"index": 1, "keep": False},
                {"index": 2, "keep": False},
                {"index": 3, "keep": True},
                {"index": 4, "keep": False},
            ]},
        ]
    )

    def _fake_llm(**_kwargs):
        return next(responses)

    monkeypatch.setattr("autosmartcut.intelligence_2b.call_llm_structured", _fake_llm)
    out = run_2b_decision(manifest, mode="block")
    assert out["keep_mask"][0]["keep"] is True
    assert out["keep_mask"][1]["keep"] is False
    assert out["keep_mask"][2]["keep"] is False
    assert out["keep_mask"][3]["keep"] is True
    assert out["keep_mask"][4]["keep"] is False


def test_run_2b_chunked_gap_block_for_uncovered_index(monkeypatch):
    manifest = {
        "goal": "g",
        "tokens": [
            {"index": 0, "text": "A"},
            {"index": 1, "text": "B"},
        ],
        "comprehension": {
            "purpose": "p",
            "outline_blocks": [{"start_index": 0, "end_index": 0, "summary": "只有0"}],
            "cleaned_annotations": [
                {"annotation_index": 0, "cleaned_content": "A"},
                {"annotation_index": 1, "cleaned_content": "B"},
            ],
        },
    }
    responses = iter(
        [
            {"decisions": [{"index": 0, "keep": True}]},
            {"decisions": [{"index": 1, "keep": False}]},
        ]
    )

    def _fake_llm(**_kwargs):
        return next(responses)

    monkeypatch.setattr("autosmartcut.intelligence_2b.call_llm_structured", _fake_llm)
    out = run_2b_decision(manifest, mode="block")
    assert out["keep_mask"][0]["keep"] is True
    assert out["keep_mask"][1]["keep"] is False
