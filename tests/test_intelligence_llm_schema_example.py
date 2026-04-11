"""_generate_json_example 递归示例：须展示数组内 item 键名（供 LLM 对齐 DeepSeek JSON 模式）。"""

import json

from autosmartcut import intelligence_llm as illm
from autosmartcut.intelligence_2a import _get_r1_schema, _get_r2_schema
from autosmartcut.intelligence_2b import _get_schema as get_2b_schema


def test_r1_example_contains_outline_block_keys():
    raw = illm._generate_json_example(_get_r1_schema())
    data = json.loads(raw)
    assert "outline_blocks_rough" in data
    assert isinstance(data["outline_blocks_rough"], list)
    assert len(data["outline_blocks_rough"]) >= 1
    block = data["outline_blocks_rough"][0]
    assert set(block.keys()) >= {"start_index", "end_index", "topic"}
    assert block["start_index"] == 0
    assert block["end_index"] == 0
    assert isinstance(block["topic"], str)
    assert "candidate_misrecognitions" in data
    assert isinstance(data["candidate_misrecognitions"], list)
    if data["candidate_misrecognitions"]:
        c0 = data["candidate_misrecognitions"][0]
        assert "annotation_index" in c0 and "wrong" in c0 and "suggestions" in c0


def test_r2_example_contains_outline_blocks_and_corrections():
    raw = illm._generate_json_example(_get_r2_schema())
    data = json.loads(raw)
    assert "outline_blocks" in data and len(data["outline_blocks"]) >= 1
    b0 = data["outline_blocks"][0]
    assert set(b0.keys()) >= {"start_index", "end_index", "summary"}
    assert "corrections" in data and isinstance(data["corrections"], list)
    if data["corrections"]:
        assert set(data["corrections"][0].keys()) >= {"index", "old", "nth", "new"}


def test_2b_example_contains_decisions_item():
    raw = illm._generate_json_example(get_2b_schema())
    data = json.loads(raw)
    assert "decisions" in data and len(data["decisions"]) >= 1
    d0 = data["decisions"][0]
    assert d0.get("index") == 0
    assert d0.get("keep") is True
