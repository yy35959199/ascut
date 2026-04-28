"""intelligence_llm 多轮与消息清洗、V4 请求参数（无真实网络）。"""

from types import SimpleNamespace

import pytest

from autosmartcut.config import AppConfig, LLMConfig, LLMStageConfig
from autosmartcut.intelligence_llm import (
    _build_chat_kwargs,
    call_structured,
    prepare_next_turn_messages,
    sanitize_messages_for_api,
)


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
        "autosmartcut.intelligence_llm.load_config",
        lambda: _dummy_llm_app_config(thinking=False),
    )

    captured: dict = {}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"x": 42}'),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                ),
            )

    class _FakeClient:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    monkeypatch.setattr("autosmartcut.intelligence_llm._get_client", lambda ak, bu: _FakeClient())
    monkeypatch.setattr("autosmartcut.intelligence_llm.time.sleep", lambda _: None)

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
