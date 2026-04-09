"""LLM 调用封装 - DeepSeek API

职责：
- 统一 LLM 调用接口
- 结构化 JSON 输出
- 重试机制
- 日志记录
- 缓存友好设计
"""

import json
import time
import logging
from typing import Any
from pathlib import Path

import toml
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


# ============================================================================
# 配置管理
# ============================================================================

def _load_config() -> dict:
    """从 config.toml 加载 LLM 配置"""
    config_path = Path(__file__).parent.parent / "config.toml"

    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    config = toml.load(config_path)

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
    """实际调用 DeepSeek API

    返回原始 response 对象
    """
    try:
        # 构造请求参数
        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"}  # 启用 JSON mode
        }

        # 如果启用 reasoning 且使用 chat 模型，通过 extra_body 传递
        if enable_reasoning and model != "deepseek-reasoner":
            response = client.chat.completions.create(
                **kwargs,
                extra_body={"thinking": {"type": "enabled"}}
            )
        else:
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

def call_llm_structured(
    prompt: str,
    schema: dict,
    temperature: float | None = None,
    enable_reasoning: bool = False,
    max_retries: int = DEFAULT_MAX_RETRIES
) -> dict:
    """调用 LLM 并返回结构化 JSON 输出

    Args:
        prompt: 用户输入的 prompt（不含 JSON 格式说明，由函数自动添加）
        schema: JSON Schema 定义（用于生成格式样例和验证输出）
        temperature: 温度参数（None 则使用默认值）
        enable_reasoning: 是否启用思考模式（deepseek-reasoner）
        max_retries: 最大重试次数

    Returns:
        解析后的 JSON 对象（dict）

    Raises:
        LLMEmptyContentError: 响应 content 为空
        LLMTokenLimitError: Token 超限
        LLMJSONParseError: JSON 解析失败
        LLMAPIError: API 调用失败
    """
    # 1. 加载配置
    config = _load_config()
    model = _get_model_name(enable_reasoning, config)
    temp = temperature if temperature is not None else config.get("default_temperature", 0.3)
    max_tokens = config.get("default_max_tokens", 8192)

    # 2. 初始化 OpenAI 客户端
    client = OpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"]
    )

    # 3. 构造 messages
    messages = _build_messages(prompt, schema)

    # 4. 重试循环
    for attempt in range(max_retries):
        try:
            # 调用 API
            response = _call_api(client, messages, model, temp, max_tokens, enable_reasoning)

            # 解析响应
            content, usage, reasoning_content = _parse_response(response, enable_reasoning)

            # 检查空 content
            if not content or content.strip() == "":
                raise LLMEmptyContentError(usage)

            # 提取 JSON
            data = _extract_json(content)

            # 验证 schema
            _validate_json(data, schema)

            # 记录日志
            _log_call(prompt, content, usage, enable_reasoning, reasoning_content)

            return data

        except LLMEmptyContentError as e:
            if attempt < max_retries - 1:
                print(f"[LLM] 空 content，重试 {attempt+1}/{max_retries}")
                time.sleep(RETRY_DELAY_SECONDS * (2 ** attempt))
                continue
            else:
                raise

        except LLMJSONParseError as e:
            if "SCHEMA_ERROR:" in str(e):
                raise
            if attempt < max_retries - 1:
                print(f"[LLM] JSON 解析失败，重试 {attempt+1}/{max_retries}")
                time.sleep(RETRY_DELAY_SECONDS * (2 ** attempt))
                continue
            else:
                raise

        except LLMAPIError as e:
            # API 错误不重试（可能是配置问题）
            raise

    raise LLMCallError("不应到达此处")


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
