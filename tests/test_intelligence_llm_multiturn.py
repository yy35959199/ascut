"""intelligence_llm 多轮与消息清洗、思考模式请求参数（无真实网络）。"""

from types import SimpleNamespace

import pytest

from autosmartcut.intelligence_llm import (
    call_turn_structured,
    prepare_next_turn_messages,
    sanitize_messages_for_api,
)


def test_sanitize_messages_strips_reasoning_content() -> None:
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": "{}", "reasoning_content": "think"},
    ]
    out = sanitize_messages_for_api(msgs)
    assert "reasoning_content" not in out[1]
    assert out[1]["content"] == "{}"


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


def test_call_turn_structured_augment_last_user_and_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    captured: dict = {}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"x": 42}')
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

    monkeypatch.setattr("autosmartcut.intelligence_llm.OpenAI", _FakeClient)
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
    out = call_turn_structured(messages, schema, enable_reasoning=False)
    assert out == {"x": 42}

    last_user = captured["kwargs"]["messages"][-1]["content"]
    assert "second task only" in last_user
    assert "JSON" in last_user or "{" in last_user
    assert "temperature" in captured["kwargs"]


def test_call_api_omits_temperature_when_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autosmartcut import intelligence_llm as m

    calls: list[dict] = []

    class _FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content='{"greeting":"hi","language":"en"}'))
                ],
                usage=SimpleNamespace(
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                ),
            )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions())
    )

    msgs = m._build_messages("p", {
        "type": "object",
        "properties": {
            "greeting": {"type": "string"},
            "language": {"type": "string"},
        },
        "required": ["greeting", "language"],
    })
    m._call_api(
        client,
        msgs,
        model="deepseek-reasoner",
        temperature=0.9,
        max_tokens=100,
        enable_reasoning=True,
    )
    assert len(calls) == 1
    assert "temperature" not in calls[0]
    assert calls[0]["response_format"] == {"type": "json_object"}
