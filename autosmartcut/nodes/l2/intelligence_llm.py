"""LLM 调用封装 - DeepSeek API V4

职责：
- 统一入口 ``call_structured``（按 config.toml 阶段配置）
- 多轮单跳 + ``prepare_next_turn_messages``（无 tool_calls 可省略历史 reasoning_content）
- 结构化 JSON 输出与 jsonschema 校验
- 思考模式通过 extra_body.thinking；reasoning_effort 为顶层参数
- 客户端单例复用、重试与缓存友好（R1+R2 共享前缀）
- 流式输出（stream=True + stream_options.include_usage）：
  - 内部始终走流式路径，通过 on_chunk 回调推送 StreamChunk 事件
  - on_chunk 可选；不传则静默收集，行为与原非流式一致
  - 重试时推送 retry chunk，通知消费方清空已显示内容

# TODO: 未来 TUI async 消费时，考虑实现 call_structured_aiter()
#       返回 AsyncIterator[StreamChunk]，供 async 事件循环逐 chunk await。
"""

from __future__ import annotations

import copy
import json
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpcore
import httpx
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


def _retryable_transport_types() -> tuple[type[BaseException], ...]:
    """httpx/httpcore 及 OpenAI 连接类异常，供 ``_is_retryable_transport_error`` 使用。"""
    types_list: list[type[BaseException]] = [
        httpx.RemoteProtocolError,
        httpx.ReadTimeout,
        httpx.ConnectTimeout,
        httpx.ConnectError,
        httpcore.RemoteProtocolError,
    ]
    for _name in ("ReadError", "WriteError", "ConnectError"):
        _t = getattr(httpcore, _name, None)
        if isinstance(_t, type) and issubclass(_t, BaseException):
            types_list.append(_t)
    try:
        from openai import APIConnectionError as _APIConnectionError
    except ImportError:
        pass
    else:
        types_list.append(_APIConnectionError)
    return tuple(types_list)


_RETRYABLE_TRANSPORT_TYPES: tuple[type[BaseException], ...] = (
    _retryable_transport_types()
)


def _is_retryable_transport_error(exc: BaseException | None) -> bool:
    """判定是否为可重试的传输层异常（含 ``__cause__`` / ``__context__`` 短链）。"""
    if exc is None:
        return False
    seen: set[int] = set()
    cur: BaseException | None = exc
    depth = 0
    while cur is not None and depth < 8:
        oid = id(cur)
        if oid in seen:
            break
        seen.add(oid)
        if isinstance(cur, _RETRYABLE_TRANSPORT_TYPES):
            return True
        cur = cur.__cause__ or cur.__context__
        depth += 1
    return False


@dataclass(frozen=True)
class StructuredResult:
    """单次结构化补全结果（供多轮衔接时取 assistant 原文与请求快照）。"""

    data: dict
    assistant_content: str
    usage: dict
    request_messages: list[dict]


@dataclass
class StreamChunk:
    """LLM 流式输出的单个事件。

    event 类型语义：
    - ``"reasoning_delta"``：thinking 模式下推理过程的增量文本（reasoning_delta 非空）
    - ``"content_delta"``  ：最终回答的增量文本（content_delta 非空）
    - ``"usage"``          ：流结束时的 token 用量（stream_options.include_usage）
    - ``"retry"``          ：本次尝试失败，即将重试（UI 应清空当前显示内容）
    - ``"result"``         ：调用成功，携带最终 StructuredResult

    delta 类事件只带 delta 字段，不带累积字符串（消费方自行维护 buffer）。
    usage 和 result 不放入 ProgressEvent.payload——usage 由 _log_llm_call 记录，
    result 由节点层处理（写 manifest + 发 stage_exit）。
    """

    stage: str
    """LLM 阶段标识，与 config.toml [llm.stages.*] 键名一致（r1/r2/decision/review/light）"""

    event: Literal["reasoning_delta", "content_delta", "usage", "retry", "result"]

    # delta 类
    reasoning_delta: str = ""
    content_delta: str = ""

    # usage 类
    usage: dict[str, Any] | None = None

    # retry 类
    attempt: int = 0
    """第几次尝试失败（1-based）"""
    retry_reason: str = ""
    """失败原因简述"""

    # result 类
    result: StructuredResult | None = None


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
    """构造 chat.completions.create 的 kwargs（DeepSeek V4，始终流式）。"""
    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "max_tokens": cfg.max_tokens,
        "response_format": {"type": "json_object"},
        "stream": True,                                # 始终流式
        "stream_options": {"include_usage": True},     # 流结束时拿 usage
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


def _extract_stream_usage(usage_obj: Any) -> dict[str, Any]:
    """从流式 usage chunk 提取 token 计数字典。"""
    usage: dict[str, Any] = {}
    if usage_obj is None:
        return usage
    for attr in ("prompt_tokens", "completion_tokens", "total_tokens"):
        val = getattr(usage_obj, attr, None)
        if val is not None:
            usage[attr] = val
    for attr in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        val = getattr(usage_obj, attr, None)
        if val is not None:
            usage[attr] = val
    details = getattr(usage_obj, "completion_tokens_details", None)
    if details is not None:
        rt = getattr(details, "reasoning_tokens", None)
        if rt is not None:
            usage["reasoning_tokens"] = rt
    return usage


def _safe_on_chunk(
    on_chunk: Callable[[StreamChunk], None],
    chunk: StreamChunk,
) -> None:
    """调用 on_chunk，捕获异常避免回调错误中断 LLM 收集。"""
    try:
        on_chunk(chunk)
    except Exception as e:
        logger.warning("[LLM] on_chunk 回调异常（忽略）: %s", e)


def _collect_stream(
    stream_response: Any,
    stage: str,
    cfg: LLMStageConfig,
    *,
    on_chunk: Callable[[StreamChunk], None] | None = None,
) -> tuple[str, str | None, dict[str, Any]]:
    """消费 SSE 流，返回 (content, reasoning_content, usage)。

    过程中通过 on_chunk 推送每个 delta 和 usage 事件。
    on_chunk 调用是同步的；_safe_on_chunk 保证回调异常不中断收集。

    finish_reason 检查：
    - ``length``                    → WARNING（达到 max_tokens 或上下文长度限制）
    - ``insufficient_system_resource`` → WARNING（系统推理资源不足，生成被打断）
    - ``content_filter``            → WARNING（触发内容过滤）
    """
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    usage: dict[str, Any] = {}
    finish_reason: str | None = None

    for chunk in stream_response:
        # usage chunk（stream_options.include_usage=True，在 [DONE] 前出现）
        if getattr(chunk, "usage", None) is not None:
            usage = _extract_stream_usage(chunk.usage)
            if on_chunk:
                _safe_on_chunk(on_chunk, StreamChunk(
                    stage=stage, event="usage", usage=usage,
                ))
            continue

        if not chunk.choices:
            continue

        choice = chunk.choices[0]

        # 记录最后一个非 None 的 finish_reason
        fr = getattr(choice, "finish_reason", None)
        if fr is not None:
            finish_reason = fr

        delta = choice.delta
        c = getattr(delta, "content", None) or ""
        r = getattr(delta, "reasoning_content", None) or ""

        if c:
            content_parts.append(c)
        if r:
            reasoning_parts.append(r)

        if on_chunk:
            if r:
                _safe_on_chunk(on_chunk, StreamChunk(
                    stage=stage, event="reasoning_delta", reasoning_delta=r,
                ))
            if c:
                _safe_on_chunk(on_chunk, StreamChunk(
                    stage=stage, event="content_delta", content_delta=c,
                ))

    # finish_reason 检查（与原 _parse_response 行为一致）
    if finish_reason == "length":
        logger.warning("[LLM] finish_reason=length（达到 max_tokens 或上下文长度限制）")
    elif finish_reason == "insufficient_system_resource":
        logger.warning(
            "[LLM] finish_reason=insufficient_system_resource（系统推理资源不足，生成被打断）"
        )
    elif finish_reason == "content_filter":
        logger.warning("[LLM] finish_reason=content_filter（触发内容过滤）")

    content = "".join(content_parts)
    reasoning = "".join(reasoning_parts) if reasoning_parts else None
    return content, reasoning, usage


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
        body: dict[str, Any] = {}
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
    on_chunk: Callable[[StreamChunk], None] | None = None,
) -> StructuredResult:
    """结构化 JSON 补全（按 config.toml [llm.stages.*]）。

    内部始终走流式（stream=True + stream_options.include_usage）。
    on_chunk 可选；传入时，每个流式事件（reasoning_delta / content_delta /
    usage / retry / result）都会同步调用一次。不传则静默收集。

    重试语义：
    - 空 content 或 JSON 解析/schema 校验失败时重试（最多 max_retries 次）
    - HTTP 429 / 503 时指数退避重试（带 jitter），同上次数上限
    - 重试前推送 retry chunk（attempt=当前失败次数，1-based），通知消费方清空显示
    - schema 本身有问题（SCHEMA_ERROR）时不重试，直接抛出
    - 其它不可恢复的 ``LLMAPIError`` 不重试，直接抛出
    """
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
            stream_response, api_kwargs = _call_api(client, msgs, cfg)
            content, reasoning_content, usage = _collect_stream(
                stream_response, stage, cfg, on_chunk=on_chunk,
            )
            _elapsed_req = time.monotonic() - _t_req
            logger.info(
                "[LLM] stage=%s 流式收集完成，耗时 %.1fs",
                stage,
                _elapsed_req,
            )

            if not content or content.strip() == "":
                raise LLMEmptyContentError(usage)

            data = _extract_json(content)
            _validate_json(data, schema)

            _log_llm_call(
                hint,
                api_kwargs,
                content,
                usage,
                cfg,
                reasoning_content,
            )

            sr = StructuredResult(
                data=data,
                assistant_content=content,
                usage=usage,
                request_messages=request_snapshot,
            )
            # 成功：推 result chunk，通知消费方该 stage 已完成
            if on_chunk:
                _safe_on_chunk(on_chunk, StreamChunk(
                    stage=stage, event="result", result=sr,
                ))
            return sr

        except LLMEmptyContentError:
            if attempt < max_retries - 1:
                if on_chunk:
                    _safe_on_chunk(on_chunk, StreamChunk(
                        stage=stage,
                        event="retry",
                        attempt=attempt + 1,
                        retry_reason="API 返回空 content",
                    ))
                logger.warning(
                    "[LLM] stage=%s 空 content，重试 %d/%d",
                    stage, attempt + 1, max_retries,
                )
                time.sleep(
                    RETRY_DELAY_SECONDS * (2 ** attempt)
                    + random.uniform(0.0, 0.5),
                )
                continue
            raise

        except LLMJSONParseError as e:
            if "SCHEMA_ERROR:" in str(e):
                raise
            if attempt < max_retries - 1:
                reason = str(e)[:120]
                if on_chunk:
                    _safe_on_chunk(on_chunk, StreamChunk(
                        stage=stage,
                        event="retry",
                        attempt=attempt + 1,
                        retry_reason=reason,
                    ))
                logger.warning(
                    "[LLM] stage=%s JSON 解析失败，重试 %d/%d: %s",
                    stage, attempt + 1, max_retries, e,
                )
                time.sleep(
                    RETRY_DELAY_SECONDS * (2 ** attempt)
                    + random.uniform(0.0, 0.5),
                )
                continue
            raise

        except LLMAPIError as e:
            sc = int(getattr(e, "status_code", 0) or 0)
            if sc in (429, 503) and attempt < max_retries - 1:
                delay = min(
                    1.0 * (2**attempt) + random.uniform(0.0, 0.5),
                    30.0,
                )
                if sc == 503:
                    delay = max(delay, 5.0)
                if on_chunk:
                    _safe_on_chunk(
                        on_chunk,
                        StreamChunk(
                            stage=stage,
                            event="retry",
                            attempt=attempt + 1,
                            retry_reason=f"HTTP {sc} 限速",
                        ),
                    )
                logger.warning(
                    "[LLM] stage=%s HTTP %d，%.1fs 后重试 %d/%d",
                    stage,
                    sc,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(delay)
                continue
            logger.error("[LLM] stage=%s API 错误: %s", stage, e)
            raise

        except Exception as e:
            if not _is_retryable_transport_error(e):
                raise
            if attempt < max_retries - 1:
                reason = str(e)[:120]
                if on_chunk:
                    _safe_on_chunk(on_chunk, StreamChunk(
                        stage=stage,
                        event="retry",
                        attempt=attempt + 1,
                        retry_reason=f"传输错误: {reason}",
                    ))
                logger.warning(
                    "[LLM] stage=%s 传输异常，重试 %d/%d: %s",
                    stage, attempt + 1, max_retries, e,
                )
                time.sleep(
                    RETRY_DELAY_SECONDS * (2 ** attempt)
                    + random.uniform(0.0, 0.5),
                )
                continue
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
