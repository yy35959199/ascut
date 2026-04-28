"""intelligence_llm 多轮与消息清洗、V4 请求参数（无真实网络）。"""

from types import SimpleNamespace

import pytest

from autosmartcut.config import AppConfig, LLMConfig, LLMStageConfig
from autosmartcut.nodes.l2.intelligence_llm import (
    _build_chat_kwargs,
    call_structured,
    prepare_next_turn_messages,
    sanitize_messages_for_api,
)

_MODULE = "autosmartcut.nodes.l2.intelligence_llm"


def _dummy_llm_app_config(
    *,
    thinking: bool = False,
    temperature: float = 0.3,
) -> AppConfig:
    stage = LLMStageConfig(
        model="deepseek-v4-flash",
        thinking=thinking,
        reasoning_effort="high",
        temperature=temperature,
        max_tokens=1024,
    )
    llm = LLMConfig(
        api_key="dummy",
        base_url="https://api.deepseek.com",
        default=stage,
        stages={
            "r2": stage,
            "r1": stage,
        },
    )
    return AppConfig(llm=llm)


def _make_stream_chunks(content: str):
    """构造模拟流式 chunk 列表（content delta + usage chunk）。"""
    yield SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(
            finish_reason=None,
            delta=SimpleNamespace(content=content, reasoning_content=None),
        )],
    )
    yield SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(
            finish_reason="stop",
            delta=SimpleNamespace(content=None, reasoning_content=None),
        )],
    )
    yield SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            prompt_cache_hit_tokens=None,
            prompt_cache_miss_tokens=None,
            completion_tokens_details=None,
        ),
        choices=[],
    )


def test_sanitize_messages_strips_reasoning_content() -> None:
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": "{}", "reasoning_content": "think"},
    ]
    out = sanitize_messages_for_api(msgs)
    assert "reasoning_content" not in out[1]
    assert out[1]["content"] == "{}"


def test_sanitize_keeps_reasoning_when_tool_calls() -> None:
    msgs = [
        {
            "role": "assistant",
            "content": "{}",
            "reasoning_content": "keep-me",
            "tool_calls": [{"id": "c", "type": "function"}],
        },
    ]
    out = sanitize_messages_for_api(msgs)
    assert out[0].get("reasoning_content") == "keep-me"


def test_prepare_next_turn_appends_assistant_and_user() -> None:
    base = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
    ]
    out = prepare_next_turn_messages(
        base,
        assistant_content='{"a":1}',
        next_user_content="请继续",
    )
    assert len(out) == 4
    assert out[2] == {"role": "assistant", "content": '{"a":1}'}
    assert out[3] == {"role": "user", "content": "请继续"}


def test_call_structured_augment_last_user_and_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        f"{_MODULE}.load_config",
        lambda: _dummy_llm_app_config(thinking=False),
    )

    captured: dict = {}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured["kwargs"] = kwargs
            # 返回流式 chunk 迭代器
            return _make_stream_chunks('{"x": 42}')

    class _FakeClient:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    monkeypatch.setattr(f"{_MODULE}._get_client", lambda ak, bu: _FakeClient())
    monkeypatch.setattr(f"{_MODULE}.time.sleep", lambda _: None)

    schema = {
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
    }
    messages = prepare_next_turn_messages(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "first"}],
        assistant_content='{"a":1}',
        next_user_content="second task only",
    )
    out = call_structured(messages, schema, "r2").data
    assert out == {"x": 42}

    last_user = captured["kwargs"]["messages"][-1]["content"]
    assert "second task only" in last_user
    assert "示例 JSON" in last_user or "JSON" in last_user
    assert captured["kwargs"]["extra_body"]["thinking"]["type"] == "disabled"
    assert "temperature" in captured["kwargs"]
    # 验证始终流式
    assert captured["kwargs"]["stream"] is True
    assert captured["kwargs"]["stream_options"] == {"include_usage": True}


def test_build_chat_kwargs_omits_temperature_when_thinking() -> None:
    cfg = LLMStageConfig(
        model="deepseek-v4-pro",
        thinking=True,
        reasoning_effort="high",
        temperature=0.2,
        max_tokens=100,
    )
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    kw = _build_chat_kwargs(msgs, cfg)
    assert "temperature" not in kw
    assert kw["reasoning_effort"] == "high"
    assert kw["extra_body"]["thinking"]["type"] == "enabled"
    assert kw["response_format"] == {"type": "json_object"}
    assert kw["stream"] is True
    assert kw["stream_options"] == {"include_usage": True}
