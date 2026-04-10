"""LLM 调用封装 - DeepSeek API

职责：
- 统一 LLM 调用接口（单轮 ``call_llm_structured`` / ``call_once_structured``）
- 多轮单跳 ``call_turn_structured`` + ``prepare_next_turn_messages``（跨轮不传 reasoning_content）
- 结构化 JSON 输出与 jsonschema 校验
- 思考模式下屏蔽无效采样参数
- 重试、日志、缓存友好（R1+R2 共享 system+首条 user 前缀）
"""

import copy
import json
import logging
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from openai import OpenAI

# ============================================================================
# 模型参数配置（便于调试，修改后立即生效）
# ============================================================================

# 系统提示词（所有调用共享，利用缓存）
SYSTEM_PROMPT = """你是一位专业的视频内容分析专家。
你的任务是根据用户提供的视频转写文本，进行语义理解和结构化分析。
请严格按照用户要求的 JSON 格式输出结果。"""

# 重试配置
DEFAULT_MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 1.0  # 重试间隔（指数退避）

# JSON 格式说明模板
JSON_FORMAT_INSTRUCTION = """

请以 JSON 格式输出，格式如下：
{format_example}

注意：
- 必须输出合法的 JSON 对象
- 字段名和类型必须严格匹配上述格式
- 不要输出任何 JSON 之外的内容
"""

# ============================================================================
# 异常类定义
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
class StructuredLLMResult:
    """单次结构化补全结果（供多轮衔接时取 assistant 原文与请求快照）"""

    data: dict
    assistant_content: str
    usage: dict
    request_messages: list[dict]


# ============================================================================
# 配置管理
# ============================================================================

def _load_config() -> dict:
    """从 config.toml 加载 LLM 配置"""
    config_path = Path(__file__).parent.parent / "config.toml"

    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with config_path.open("rb") as f:
        config = tomllib.load(f)

    if "llm" not in config:
        raise ValueError("config.toml 中缺少 [llm] 配置段")

    llm_config = config["llm"]

    # 验证必需字段
    required_fields = ["api_key", "base_url", "model"]
    for field in required_fields:
        if field not in llm_config:
            raise ValueError(f"config.toml [llm] 中缺少必需字段: {field}")

    return llm_config


def _get_model_name(enable_reasoning: bool, config: dict) -> str:
    """根据 reasoning 标志返回模型名称"""
    if enable_reasoning:
        return config.get("reasoner_model", "deepseek-reasoner")
    else:
        return config.get("model", "deepseek-chat")


def _is_thinking_request(enable_reasoning: bool, _model: str) -> bool:
    """是否走思考模式请求路径（需屏蔽采样参数，见 DeepSeek 文档 3.1）"""
    return bool(enable_reasoning)


def _use_thinking_extra_body(model: str) -> bool:
    """是否在 chat 模型上用 ``extra_body.thinking``（与 ``deepseek-reasoner`` 模型互斥）"""
    if model == "deepseek-reasoner" or str(model).endswith("reasoner"):
        return False
    return True


def sanitize_messages_for_api(
    messages: list[dict],
    *,
    strip_reasoning: bool = True,
) -> list[dict]:
    """深拷贝消息列表；跨轮发送前移除不应带入下一轮的 reasoning 等字段。

    DeepSeek：新一轮对话不应拼接历史轮次的 ``reasoning_content``。
    """
    out: list[dict] = []
    for m in messages:
        d = dict(m)
        if strip_reasoning:
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


def build_once_messages(prompt: str, schema: dict) -> list[dict]:
    """构造单轮请求的 messages（system + user，已含 JSON 格式说明）。供调用方做多轮前缀复用。"""
    return _build_messages(prompt, schema)


# ============================================================================
# Prompt 构造
# ============================================================================

def _build_messages(prompt: str, schema: dict) -> list[dict]:
    """构造 messages 列表

    返回格式：
    [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt + JSON格式说明}
    ]
    """
    # 生成 JSON 格式样例
    json_example = _generate_json_example(schema)

    # 拼接用户消息（包含 JSON 格式说明）
    user_content = prompt + JSON_FORMAT_INSTRUCTION.format(format_example=json_example)

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content}
    ]


def _generate_json_example(schema: dict) -> str:
    """根据 JSON Schema 生成格式样例

    简化实现：直接将 schema 转为格式化的 JSON 字符串
    """
    # 从 schema 提取示例结构
    if "properties" in schema:
        example = {}
        for key, value in schema["properties"].items():
            prop_type = value.get("type", "string")

            if prop_type == "string":
                example[key] = f"<{key}>"
            elif prop_type == "number" or prop_type == "integer":
                example[key] = 0
            elif prop_type == "boolean":
                example[key] = True
            elif prop_type == "array":
                example[key] = []
            elif prop_type == "object":
                example[key] = {}
            else:
                example[key] = None

        return json.dumps(example, indent=2, ensure_ascii=False)
    else:
        # 如果没有 properties，直接返回 schema
        return json.dumps(schema, indent=2, ensure_ascii=False)


# ============================================================================
# API 调用
# ============================================================================

def _call_api(
    client: OpenAI,
    messages: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
    enable_reasoning: bool
) -> dict:
    """实际调用 DeepSeek API（非流式）

    思考模式下按文档屏蔽 temperature / top_p 等采样参数，避免误传无效字段。
    """
    try:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }

        if _is_thinking_request(enable_reasoning, model):
            if _use_thinking_extra_body(model):
                kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            kwargs["temperature"] = temperature

        response = client.chat.completions.create(**kwargs)

        return response

    except Exception as e:
        # 捕获 API 错误
        if hasattr(e, 'status_code'):
            raise LLMAPIError(e.status_code, str(e))
        else:
            raise LLMAPIError(0, str(e))


# ============================================================================
# 响应处理
# ============================================================================

def _augment_last_user_with_schema(messages: list[dict], schema: dict) -> None:
    """在 messages 副本上，为最后一条 user 追加 JSON Schema 格式说明。"""
    json_example = _generate_json_example(schema)
    suffix = JSON_FORMAT_INSTRUCTION.format(format_example=json_example)
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            content = messages[i].get("content") or ""
            messages[i] = {**messages[i], "content": content + suffix}
            return
    raise ValueError("messages 中需要至少一条 role=user 的消息")


def _parse_response(response: dict, enable_reasoning: bool) -> tuple[str, dict, str | None]:
    """解析 API 响应

    Returns:
        (content_text, usage_info, reasoning_content)
    """
    choice = response.choices[0]
    message = choice.message

    # 提取 content
    content = message.content or ""

    # 提取 reasoning_content（如果有）
    reasoning_content = None
    if enable_reasoning and hasattr(message, 'reasoning_content'):
        reasoning_content = message.reasoning_content

    # 提取 usage 信息
    usage = {
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
        "total_tokens": response.usage.total_tokens,
    }

    # 提取缓存命中信息（如果有）
    if hasattr(response.usage, 'prompt_cache_hit_tokens'):
        usage["prompt_cache_hit_tokens"] = response.usage.prompt_cache_hit_tokens
    if hasattr(response.usage, 'prompt_cache_miss_tokens'):
        usage["prompt_cache_miss_tokens"] = response.usage.prompt_cache_miss_tokens

    return content, usage, reasoning_content


def _extract_json(content: str) -> dict:
    """从 content 中提取 JSON 对象

    处理可能的情况：
    - 纯 JSON
    - Markdown 代码块包裹的 JSON
    - 前后有多余文字的 JSON
    """
    content = content.strip()

    # 尝试直接解析
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 尝试提取 Markdown 代码块
    if "```json" in content:
        start = content.find("```json") + 7
        end = content.find("```", start)
        if end != -1:
            json_str = content[start:end].strip()
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

    # 尝试提取 {} 包裹的内容
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        json_str = content[start:end+1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    # 所有尝试都失败
    raise LLMJSONParseError(content, "无法从响应中提取合法 JSON")


def _validate_json(data: dict, schema: dict) -> None:
    """验证 JSON 是否符合 schema（Draft 2020-12）"""
    logger = logging.getLogger(__name__)

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as e:
        raise LLMJSONParseError(
            json.dumps(schema, ensure_ascii=False),
            f"SCHEMA_ERROR: 无效 JSON Schema ({e.message})"
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
        formatted_errors.append(
            f"{err_path}: {err.message} (validator={err.validator})"
        )

    first_path = _format_path(list(errors[0].path))
    logger.warning("[LLM] Schema 校验失败，首条路径: %s", first_path)

    raise LLMJSONParseError(
        json.dumps(data, ensure_ascii=False),
        "INSTANCE_ERROR: 输出不符合 JSON Schema; " + "; ".join(formatted_errors)
    )


# ============================================================================
# 日志记录
# ============================================================================

def _log_call(
    prompt: str,
    response_content: str,
    usage: dict,
    enable_reasoning: bool,
    reasoning_content: str | None = None
) -> None:
    """记录 LLM 调用日志"""
    logger = logging.getLogger(__name__)

    # 截断 prompt 显示
    prompt_preview = prompt[:500] + "..." if len(prompt) > 500 else prompt

    logger.info("="*80)
    logger.info("[LLM 调用]")
    logger.info(f"Prompt (前500字符): {prompt_preview}")
    logger.info(f"响应: {response_content[:500]}...")
    logger.info(f"Token 使用: {usage}")

    if enable_reasoning and reasoning_content:
        logger.info(f"思考链 (前200字符): {reasoning_content[:200]}...")

    # 缓存命中统计
    if "prompt_cache_hit_tokens" in usage:
        hit = usage["prompt_cache_hit_tokens"]
        miss = usage["prompt_cache_miss_tokens"]
        total = hit + miss
        hit_rate = (hit / total * 100) if total > 0 else 0
        logger.info(f"缓存命中率: {hit_rate:.1f}% ({hit}/{total} tokens)")

    logger.info("="*80)


# ============================================================================
# 核心接口
# ============================================================================

def _complete_structured(
    messages: list[dict],
    schema: dict,
    *,
    augment_last_user: bool,
    temperature: float | None,
    enable_reasoning: bool,
    max_retries: int,
    log_prompt_hint: str,
) -> StructuredLLMResult:
    """执行一次结构化补全（内部共用：单轮已带 schema / 多轮仅最后一跳追加 schema）。"""
    request_snapshot = copy.deepcopy(messages)
    msgs = copy.deepcopy(messages)
    if augment_last_user:
        _augment_last_user_with_schema(msgs, schema)

    config = _load_config()
    model = _get_model_name(enable_reasoning, config)
    temp = temperature if temperature is not None else config.get("default_temperature", 0.3)
    max_tokens = config.get("default_max_tokens", 8192)

    client = OpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"]
    )

    for attempt in range(max_retries):
        try:
            response = _call_api(client, msgs, model, temp, max_tokens, enable_reasoning)

            content, usage, reasoning_content = _parse_response(response, enable_reasoning)

            if not content or content.strip() == "":
                raise LLMEmptyContentError(usage)

            data = _extract_json(content)
            _validate_json(data, schema)

            _log_call(log_prompt_hint, content, usage, enable_reasoning, reasoning_content)

            return StructuredLLMResult(
                data=data,
                assistant_content=content,
                usage=usage,
                request_messages=request_snapshot,
            )

        except LLMEmptyContentError:
            if attempt < max_retries - 1:
                print(f"[LLM] 空 content，重试 {attempt+1}/{max_retries}")
                time.sleep(RETRY_DELAY_SECONDS * (2 ** attempt))
                continue
            raise

        except LLMJSONParseError as e:
            if "SCHEMA_ERROR:" in str(e):
                raise
            if attempt < max_retries - 1:
                print(f"[LLM] JSON 解析失败，重试 {attempt+1}/{max_retries}")
                time.sleep(RETRY_DELAY_SECONDS * (2 ** attempt))
                continue
            raise

        except LLMAPIError:
            raise

    raise LLMCallError("不应到达此处")


def call_llm_structured(
    prompt: str,
    schema: dict,
    temperature: float | None = None,
    enable_reasoning: bool = False,
    max_retries: int = DEFAULT_MAX_RETRIES
) -> dict:
    """单轮调用 LLM 并返回结构化 JSON（system + user 由本函数构造）。"""
    messages = _build_messages(prompt, schema)
    return _complete_structured(
        messages,
        schema,
        augment_last_user=False,
        temperature=temperature,
        enable_reasoning=enable_reasoning,
        max_retries=max_retries,
        log_prompt_hint=prompt,
    ).data


def call_once_structured(
    prompt: str,
    schema: dict,
    temperature: float | None = None,
    enable_reasoning: bool = False,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict:
    """``call_llm_structured`` 的别名，语义上强调「单轮」。"""
    return call_llm_structured(
        prompt=prompt,
        schema=schema,
        temperature=temperature,
        enable_reasoning=enable_reasoning,
        max_retries=max_retries,
    )


def call_once_structured_with_raw_content(
    prompt: str,
    schema: dict,
    temperature: float | None = None,
    enable_reasoning: bool = False,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> StructuredLLMResult:
    """单轮结构化补全，并返回 assistant 原始 JSON 字符串与请求 messages 快照（供多轮衔接）。"""
    messages = _build_messages(prompt, schema)
    return _complete_structured(
        messages,
        schema,
        augment_last_user=False,
        temperature=temperature,
        enable_reasoning=enable_reasoning,
        max_retries=max_retries,
        log_prompt_hint=prompt,
    )


def call_turn_structured(
    messages: list[dict],
    schema: dict,
    temperature: float | None = None,
    enable_reasoning: bool = False,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict:
    """多轮中的单回合：``messages`` 须已含完整前缀；本函数仅为最后一条 user 追加 JSON 格式说明后请求。

    调用方应使用 ``prepare_next_turn_messages`` 拼接历史，且勿将历史轮的 ``reasoning_content`` 传入。
    """
    sanitized = sanitize_messages_for_api([dict(m) for m in messages])
    hint = ""
    for m in reversed(sanitized):
        if m.get("role") == "user":
            c = m.get("content")
            hint = (c[:500] + "...") if isinstance(c, str) and len(c) > 500 else (c or "")
            break
    return _complete_structured(
        sanitized,
        schema,
        augment_last_user=True,
        temperature=temperature,
        enable_reasoning=enable_reasoning,
        max_retries=max_retries,
        log_prompt_hint=hint or "(multi-turn)",
    ).data


# ============================================================================
# 测试函数
# ============================================================================

def test_hello_world():
    """测试 DeepSeek API 连接"""
    print("\n" + "="*80)
    print("测试 DeepSeek API 连接")
    print("="*80 + "\n")

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # 简单的测试 schema
    schema = {
        "type": "object",
        "properties": {
            "greeting": {"type": "string"},
            "language": {"type": "string"}
        },
        "required": ["greeting", "language"]
    }

    # 测试 prompt
    prompt = "请用中文说 'Hello World'，并告诉我这是什么语言。"

    try:
        result = call_llm_structured(
            prompt=prompt,
            schema=schema,
            temperature=0.3,
            enable_reasoning=False
        )

        print("\n✓ API 调用成功！")
        print(f"返回结果: {json.dumps(result, indent=2, ensure_ascii=False)}")

    except Exception as e:
        print(f"\n✗ API 调用失败: {e}")
        raise


if __name__ == "__main__":
    test_hello_world()
