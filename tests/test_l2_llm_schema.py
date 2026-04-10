"""Layer 2 公共 LLM 封装（intelligence_llm）的 schema / 重试行为单测，无 torch。"""

from types import SimpleNamespace

import pytest

from autosmartcut.intelligence_llm import (
    LLMJSONParseError,
    call_llm_structured,
    _validate_json,
)


def _decision_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "keep": {"type": "boolean"},
                    },
                    "required": ["index", "keep"],
                },
            }
        },
        "required": ["decisions"],
    }


def test_validate_json_passes_for_valid_instance() -> None:
    schema = _decision_schema()
    data = {"decisions": [{"index": 0, "keep": True}]}
    _validate_json(data, schema)


def test_validate_json_fails_on_missing_required() -> None:
    schema = _decision_schema()
    data = {}
    with pytest.raises(LLMJSONParseError, match="INSTANCE_ERROR"):
        _validate_json(data, schema)


def test_validate_json_fails_on_type_mismatch() -> None:
    schema = _decision_schema()
    data = {"decisions": [{"index": 0, "keep": "true"}]}
    with pytest.raises(LLMJSONParseError, match="INSTANCE_ERROR"):
        _validate_json(data, schema)


def test_validate_json_fails_on_items_shape() -> None:
    schema = _decision_schema()
    data = {"decisions": [{"index": 0}]}
    with pytest.raises(LLMJSONParseError, match="INSTANCE_ERROR"):
        _validate_json(data, schema)


def test_validate_json_fails_on_invalid_schema() -> None:
    invalid_schema = {
        "type": "object",
        "properties": {
            "x": {"type": "not-a-valid-type"}
        },
    }
    with pytest.raises(LLMJSONParseError, match="SCHEMA_ERROR"):
        _validate_json({"x": 1}, invalid_schema)


def test_call_llm_structured_schema_error_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "autosmartcut.intelligence_llm._load_config",
        lambda: {
            "api_key": "dummy",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "reasoner_model": "deepseek-reasoner",
            "default_temperature": 0.3,
            "default_max_tokens": 1024,
        },
    )
    monkeypatch.setattr(
        "autosmartcut.intelligence_llm.OpenAI",
        lambda **kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "autosmartcut.intelligence_llm._call_api",
        lambda *args, **kwargs: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"x": 1}'))],
            usage=SimpleNamespace(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            ),
        ),
    )

    bad_schema = {"type": "object", "properties": {"x": {"type": "unknown-type"}}}
    with pytest.raises(LLMJSONParseError, match="SCHEMA_ERROR"):
        call_llm_structured(prompt="test", schema=bad_schema, max_retries=3)


def test_call_llm_structured_instance_error_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "autosmartcut.intelligence_llm._load_config",
        lambda: {
            "api_key": "dummy",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "reasoner_model": "deepseek-reasoner",
            "default_temperature": 0.3,
            "default_max_tokens": 1024,
        },
    )
    monkeypatch.setattr(
        "autosmartcut.intelligence_llm.OpenAI",
        lambda **kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr("autosmartcut.intelligence_llm.time.sleep", lambda _: None)

    call_count = {"n": 0}

    def _fake_call_api(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            content = '{"decisions":[{"index":0,"keep":"true"}]}'
        else:
            content = '{"decisions":[{"index":0,"keep":true}]}'
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            ),
        )

    monkeypatch.setattr("autosmartcut.intelligence_llm._call_api", _fake_call_api)

    out = call_llm_structured(
        prompt="test",
        schema=_decision_schema(),
        max_retries=3,
    )
    assert out["decisions"][0]["keep"] is True
    assert call_count["n"] == 3
