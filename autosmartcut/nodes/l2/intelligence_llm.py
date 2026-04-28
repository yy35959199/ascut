"""LLM 调用封装 - DeepSeek API V4

职责：
- 统一入口 ``call_structured``（按 config.toml 阶段配置）
- 多轮单跳 + ``prepare_next_turn_messages``（无 tool_calls 可省略历史 reasoning_content）
- 结构化 JSON 输出与 jsonschema 校验
- 思考模式通过 extra_body.thinking；reasoning_effort 为顶层参数
- 客户端单例复用、重试与缓存友好（R1+R2 共享前缀）
"""

from __future__ import annotations

import copy
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from openai import OpenAI

from autosmartcut.config import LLMStageConfig, load_config
from autosmartcut.log import log_lazy_json

logger = logging.getLogger(__name__)

# ============================================================================
# 系统提示与常量
# ============================================================================

SYSTEM_PROMPT = """【系统背景·非指令，仅供定位】
你是视频剪辑语义处理管道的一部分，整体管道含：理解层（2a，两轮 LLM：粗理解/误识候选 R1，精化主旨与纠错表 R2）→ 决策层（2b，逐句 keep/cut）→ 执行层（Layer 3，程序按 keep_mask 裁切视频，无需 LLM）。
每次调用前，用户消息会写明当前阶段与本次任务；请严格按用户消息中的指令执行。本段仅为背景说明，不是本轮要执行的操作清单。
【背景结束】

【输出纪律·须严格遵守，与 DeepSeek JSON 模式一致】
- 只输出**一个** JSON 对象；不要 Markdown 围栏、不要 JSON 以外的说明文字。
- 用户消息末尾会给出「示例 JSON」：**键名与嵌套层级必须与该示例完全一致**；禁止用同义键名替代（例如区间须用示例里的整数字段名，不得另造字段名表达同一含义）。
- 若示例与 JSON Schema 有歧义，以能通过 Schema 校验为准，且键名仍须与示例一致。
- **严禁修改原文任何字符**：句面在 JSON 中须与输入逐字一致；文字纠错只能通过理解层输出的结构化纠错表（corrections 等）由程序替换完成，不得直接在输出中改写用户可见的转写句面。"""

DEFAULT_MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 1.0

JSON_FORMAT_INSTRUCTION = """

请**只**输出一个 JSON 对象，结构须与下方「示例 JSON」**键名一致、嵌套一致**（与 API 的 JSON 模式配合；不在此处输出除 JSON 外的任何字符）：
{format_example}

注意：
- 字段名、类型、数组元素形状必须与示例一致；**不得**用其它字段名表达同一信息。
- 必须是可以被 `json.loads` 解析的合法 JSON。
"""

# ============================================================================
# 异常类
# ============================================================================


class LLMCallError(Exception):
    """LLM 调用基础异常"""

    pass


class LLMEmptyContentError(LLMCallError):
    """响应 content 为空"""

    def __init__(self, usage: dict):
        self.usage = usage
        super().__init__(f"API 返回空 content，usage: {usage}")


class LLMTokenLimitError(LLMCallError):
    """Token 超限"""

    def __init__(self, message: str):
        super().__init__(f"Token 超限: {message}")


class LLMJSONParseError(LLMCallError):
    """JSON 解析失败"""

    def __init__(self, content: str, error: str):
        self.content = content
        super().__init__(f"JSON 解析失败: {error}\n原始内容: {content[:200]}...")


class LLMAPIError(LLMCallError):
    """API 调用失败"""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"API 错误 [{status_code}]: {message}")


@dataclass(frozen=True)
class StructuredResult:
    """单次结构化补全结果（供多轮衔接时取 assistant 原文与请求快照）。"""

    data: dict
    assistant_content: str
    usage: dict
    request_messages: list[dict]


# ============================================================================
# OpenAI 客户端缓存
# ============================================================================

_client_cache: dict[tuple[str, str], OpenAI] = {}


def _get_client(api_key: str, base_url: str) -> OpenAI:
    key = (api_key, base_url)
    if key not in _client_cache:
        _client_cache[key] = OpenAI(api_key=api_key, base_url=base_url)
    return _client_cache[key]


def sanitize_messages_for_api(
    messages: list[dict],
    *,
    strip_reasoning: bool = True,
) -> list[dict]:
    """深拷贝消息列表；跨轮发送前移除 reasoning_content（无 tool_calls 时）。

    V4：有 tool_calls 的 assistant 消息必须保留 reasoning_content。
    """
    out: list[dict] = []
    for m in messages:
        d = dict(m)
        has_tool_calls = bool(d.get("tool_calls"))
        if strip_reasoning and not has_tool_calls:
            d.pop("reasoning_content", None)
        out.append(d)
    return out


def prepare_next_turn_messages(
    messages_so_far: list[dict],
    *,
    assistant_content: str,
    next_user_content: str,
    strip_reasoning: bool = True,
) -> list[dict]:
    """在已有前缀消息后追加 assistant（仅 content）与下一回合 user。"""
    base = sanitize_messages_for_api(
        [dict(x) for x in messages_so_far],
        strip_reasoning=strip_reasoning,
    )
    base.append({"role": "assistant", "content": assistant_content})
    base.append({"role": "user", "content": next_user_content})
    return base


def build_messages(prompt: str, schema: dict) -> list[dict]:
    """构造单轮请求的 messages（system + user，已含 JSON 格式说明）。供多轮前缀复用。"""
    return _build_messages(prompt, schema)


# ============================================================================
# Prompt 构造
# ============================================================================


def _build_messages(prompt: str, schema: dict) -> list[dict]:
    json_example = _generate_json_example(schema)
    user_content = prompt + JSON_FORMAT_INSTRUCTION.format(format_example=json_example)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _schema_to_example(schema: dict[str, Any], *, field_hint: str = "") -> Any:
    """由 JSON Schema 片段递归生成**一条**可展示的示例值（数组至少含一个元素）。"""
    if not isinstance(schema, dict):
        return None
    if "const" in schema:
        return schema["const"]
    if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
        return schema["enum"][0]

    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)
    if t is None and "properties" in schema:
        t = "object"

    if t == "array":
        items = schema.get("items")
        if isinstance(items, dict) and items:
            return [_schema_to_example(items, field_hint=field_hint)]
        return []

    if t == "object" or "properties" in schema:
        props = schema.get("properties")
        if not isinstance(props, dict):
            return {}
        out: dict[str, Any] = {}
        for key, sub in props.items():
            out[key] = _schema_to_example(sub, field_hint=key)
        return out

    if t == "string":
        return f"<{field_hint}>" if field_hint else "<string>"
    if t == "integer":
        return 0
    if t == "number":
        return 0.0
    if t == "boolean":
        return True
    return None


def _generate_json_example(schema: dict[str, Any]) -> str:
    """根据 JSON Schema 生成格式样例（递归；数组含一条示例元素）。"""
    if "properties" in schema:
        example_obj = _schema_to_example(schema, field_hint="")
        if not isinstance(example_obj, dict):
            example_obj = {}
        return json.dumps(example_obj, indent=2, ensure_ascii=False)
    return json.dumps(schema, indent=2, ensure_ascii=False)


def _augment_last_user_with_schema(messages: list[dict], schema: dict) -> None:
    json_example = _generate_json_example(schema)
    suffix = JSON_FORMAT_INSTRUCTION.format(format_example=json_example)
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            content = messages[i].get("content") or ""
            messages[i] = {**messages[i], "content": content + suffix}
            return
    raise ValueError("messages 中需要至少一条 role=user 的消息")


def _last_user_needs_schema_augment(messages: list[dict]) -> bool:
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            c = messages[i].get("content") or ""
            return "示例 JSON" not in c
    raise ValueError("messages 中需要至少一条 role=user 的消息")


# ============================================================================
# API
# ============================================================================


def _build_chat_kwargs(messages: list[dict], cfg: LLMStageConfig) -> dict[str, Any]:
    """构造 chat.completions.create 的 kwargs（DeepSeek V4）。"""
    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "max_tokens": cfg.max_tokens,
        "response_format": {"type": "json_object"},
        "extra_body": {
            "thinking": {"type": "enabled" if cfg.thinking else "disabled"},
        },
    }
    if cfg.thinking:
        kwargs["reasoning_effort"] = cfg.reasoning_effort
    else:
        kwargs["temperature"] = cfg.temperature
    return kwargs


def _call_api(
    client: OpenAI,
    messages: list[dict],
    cfg: LLMStageConfig,
) -> tuple[Any, dict[str, Any]]:
    kwargs = _build_chat_kwargs(messages, cfg)
    try:
        response = client.chat.completions.create(**kwargs)
        return response, kwargs
    except Exception as e:
        if hasattr(e, "status_code"):
            raise LLMAPIError(e.status_code, str(e))
        raise LLMAPIError(0, str(e))


def _response_to_log_dict(response: Any) -> dict[str, Any]:
    try:
        if hasattr(response, "model_dump"):
            raw = response.model_dump(mode="python")  # type: ignore[union-attr]
            return json.loads(json.dumps(raw, default=str))
    except Exception:
        pass
    return {"repr": str(response)[:8000]}


def _parse_response(response: Any, cfg: LLMStageConfig) -> tuple[str, dict[str, Any], str | None]:
    choice = response.choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason == "length":
        logger.warning("[LLM] finish_reason=length（达到 max_tokens 或上下文长度限制）")
    elif finish_reason == "insufficient_system_resource":
        logger.warning(
            "[LLM] finish_reason=insufficient_system_resource（系统推理资源不足，生成被打断）"
        )

    message = choice.message
    content = message.content or ""

    reasoning_content = None
    if hasattr(message, "reasoning_content"):
        reasoning_content = message.reasoning_content

    usage: dict[str, Any] = {
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
        "total_tokens": response.usage.total_tokens,
    }

    if hasattr(response.usage, "prompt_cache_hit_tokens"):
        usage["prompt_cache_hit_tokens"] = response.usage.prompt_cache_hit_tokens
    if hasattr(response.usage, "prompt_cache_miss_tokens"):
        usage["prompt_cache_miss_tokens"] = response.usage.prompt_cache_miss_tokens

    if hasattr(response.usage, "completion_tokens_details"):
        details = response.usage.completion_tokens_details
        if details is not None and hasattr(details, "reasoning_tokens"):
            rt = details.reasoning_tokens
            if rt is not None:
                usage["reasoning_tokens"] = rt

    return content, usage, reasoning_content


def _extract_json(content: str) -> dict:
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    if "```json" in content:
        start = content.find("```json") + 7
        end = content.find("```", start)
        if end != -1:
            json_str = content[start:end].strip()
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        json_str = content[start : end + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    raise LLMJSONParseError(content, "无法从响应中提取合法 JSON")


def _validate_json(data: dict, schema: dict) -> None:
    """验证 JSON 是否符合 schema（Draft 2020-12）"""
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as e:
        raise LLMJSONParseError(
            json.dumps(schema, ensure_ascii=False),
            f"SCHEMA_ERROR: 无效 JSON Schema ({e.message})",
        )

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda err: list(err.path))
    if not errors:
        return

    def _format_path(path_parts: list[Any]) -> str:
        path = "$"
        for part in path_parts:
            if isinstance(part, int):
                path += f"[{part}]"
            else:
                path += f".{part}"
        return path

    formatted_errors = []
    for err in errors[:5]:
        err_path = _format_path(list(err.path))
        formatted_errors.append(f"{err_path}: {err.message} (validator={err.validator})")

    first_path = _format_path(list(errors[0].path))
    logger.warning("[LLM] Schema 校验失败，首条路径: %s", first_path)

    raise LLMJSONParseError(
        json.dumps(data, ensure_ascii=False),
        "INSTANCE_ERROR: 输出不符合 JSON Schema; " + "; ".join(formatted_errors),
    )


def _log_llm_call(
    hint: str,
    api_kwargs: dict[str, Any],
    response: Any,
    parsed_content: str,
    usage: dict[str, Any],
    cfg: LLMStageConfig,
    reasoning_content: str | None,
) -> None:
    logger.info(
        "[LLM] %s | model=%s | prompt_tokens=%s completion_tokens=%s total=%s",
        hint,
        api_kwargs.get("model"),
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        usage.get("total_tokens"),
    )
    if "reasoning_tokens" in usage:
        logger.info("[LLM] %s | reasoning_tokens=%s", hint, usage["reasoning_tokens"])
    if "prompt_cache_hit_tokens" in usage:
        hit = usage["prompt_cache_hit_tokens"]
        miss = usage.get("prompt_cache_miss_tokens", 0)
        total = hit + miss
        hit_rate = (hit / total * 100) if total > 0 else 0.0
        logger.info(
            "[LLM] %s | 缓存命中: %.1f%% (%s/%s tokens)",
            hint,
            hit_rate,
            hit,
            total,
        )
    if cfg.thinking and reasoning_content:
        logger.info(
            "[LLM] %s | reasoning_content 长度=%d（全文见 DEBUG 日志）",
            hint,
            len(reasoning_content),
        )

    log_lazy_json(
        "LLM",
        f"{hint} 请求体(api_kwargs，含 messages)",
        lambda: dict(api_kwargs),
    )

    def _resp_payload() -> dict[str, Any]:
        body = _response_to_log_dict(response)
        body["parsed_content"] = parsed_content
        if reasoning_content is not None:
            body["reasoning_content"] = reasoning_content
        body["usage"] = usage
        return body

    log_lazy_json("LLM", f"{hint} 响应体", _resp_payload)


def _log_prompt_hint_from_messages(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str) and len(c) > 500:
                return c[:500] + "..."
            return c or ""
    return "(multi-turn)"


def call_structured(
    messages: list[dict],
    schema: dict,
    stage: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> StructuredResult:
    """结构化 JSON 补全（按 config.toml [llm.stages.*]）。"""
    app_cfg = load_config()
    if app_cfg.llm is None:
        raise ValueError("config.toml 缺少 [llm] 配置，无法调用 LLM")

    cfg = app_cfg.llm.for_stage(stage)
    llm_cfg = app_cfg.llm

    request_snapshot = copy.deepcopy(messages)
    msgs = copy.deepcopy(messages)
    msgs = sanitize_messages_for_api([dict(m) for m in msgs])
    if _last_user_needs_schema_augment(msgs):
        _augment_last_user_with_schema(msgs, schema)

    client = _get_client(llm_cfg.api_key, llm_cfg.base_url)
    hint = _log_prompt_hint_from_messages(msgs)

    for attempt in range(max_retries):
        try:
            logger.info(
                "[LLM] stage=%s model=%s thinking=%s 发起请求（attempt %d/%d）",
                stage,
                cfg.model,
                cfg.thinking,
                attempt + 1,
                max_retries,
            )
            _t_req = time.monotonic()
            response, api_kwargs = _call_api(client, msgs, cfg)
            _elapsed_req = time.monotonic() - _t_req
            logger.info(
                "[LLM] stage=%s 响应返回，耗时 %.1fs",
                stage,
                _elapsed_req,
            )
            content, usage, reasoning_content = _parse_response(response, cfg)

            if not content or content.strip() == "":
                raise LLMEmptyContentError(usage)

            data = _extract_json(content)
            _validate_json(data, schema)

            _log_llm_call(
                hint,
                api_kwargs,
                response,
                content,
                usage,
                cfg,
                reasoning_content,
            )

            return StructuredResult(
                data=data,
                assistant_content=content,
                usage=usage,
                request_messages=request_snapshot,
            )

        except LLMEmptyContentError:
            if attempt < max_retries - 1:
                logger.warning(
                    "[LLM] stage=%s 空 content，重试 %d/%d",
                    stage, attempt + 1, max_retries,
                )
                time.sleep(RETRY_DELAY_SECONDS * (2**attempt))
                continue
            raise

        except LLMJSONParseError as e:
            if "SCHEMA_ERROR:" in str(e):
                raise
            if attempt < max_retries - 1:
                logger.warning(
                    "[LLM] stage=%s JSON 解析失败，重试 %d/%d: %s",
                    stage, attempt + 1, max_retries, e,
                )
                time.sleep(RETRY_DELAY_SECONDS * (2**attempt))
                continue
            raise

        except LLMAPIError as e:
            logger.error("[LLM] stage=%s API 错误: %s", stage, e)
            raise

    raise LLMCallError("不应到达此处")


def test_hello_world() -> None:
    """测试 DeepSeek API 连接"""
    print("\n" + "=" * 80)
    print("测试 DeepSeek API 连接")
    print("=" * 80 + "\n")

    from autosmartcut.log import attach_stderr_if_unconfigured

    attach_stderr_if_unconfigured(verbose=True)

    schema = {
        "type": "object",
        "properties": {
            "greeting": {"type": "string"},
            "language": {"type": "string"},
        },
        "required": ["greeting", "language"],
    }
    prompt = "请用中文说 'Hello World'，并告诉我这是什么语言。"

    try:
        result = call_structured(build_messages(prompt, schema), schema, "r1")
        print("\n✓ API 调用成功！")
        print(json.dumps(result.data, indent=2, ensure_ascii=False))

    except Exception as e:
        print(f"\n✗ API 调用失败: {e}")
        raise


if __name__ == "__main__":
    test_hello_world()
