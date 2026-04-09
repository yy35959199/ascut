# DeepSeek API 完整文档

> 来源：https://api-docs.deepseek.com/zh-cn/
> 整理日期：2026-04-09
> 代码样例统一选用 Python（OpenAI SDK 兼容格式）

---

## 目录

1. [快速开始](#1-快速开始)
2. [API 端点参考](#2-api-端点参考)
   - 2.1 [对话补全](#21-对话补全-chatcompletions)
   - 2.2 [FIM 补全（Beta）](#22-fim-补全beta)
   - 2.3 [列出模型](#23-列出模型)
   - 2.4 [查询余额](#24-查询余额)
3. [功能指南](#3-功能指南)
   - 3.1 [思考模式](#31-思考模式)
   - 3.2 [多轮对话](#32-多轮对话)
   - 3.3 [对话前缀续写（Beta）](#33-对话前缀续写beta)
   - 3.4 [FIM 补全指南](#34-fim-补全指南)
   - 3.5 [JSON Output](#35-json-output)
   - 3.6 [Tool Calls](#36-tool-calls)
   - 3.7 [上下文硬盘缓存](#37-上下文硬盘缓存)
   - 3.8 [Anthropic API 兼容](#38-anthropic-api-兼容)
4. [参数与限制](#4-参数与限制)
   - 4.1 [Temperature 设置](#41-temperature-设置)
   - 4.2 [限速](#42-限速)
   - 4.3 [错误码](#43-错误码)

---

## 1. 快速开始

DeepSeek API 使用与 OpenAI 兼容的 API 格式，通过修改配置，您可以使用 OpenAI SDK 来访问 DeepSeek API，或使用与 OpenAI API 兼容的软件。

| 参数 | 值 |
|------|-----|
| base_url | `https://api.deepseek.com` |
| api_key | 前往 [API Keys](https://platform.deepseek.com/api_keys) 申请 |

> 出于与 OpenAI 兼容考虑，您也可以将 base_url 设置为 `https://api.deepseek.com/v1` 来使用，但注意此处 v1 与模型版本无关。

**模型说明：**
- `deepseek-chat` 和 `deepseek-reasoner` 对应模型版本为 DeepSeek-V3.2（128K 上下文长度），与 APP/WEB 版不同。
- `deepseek-chat` 对应 DeepSeek-V3.2 的**非思考模式**。
- `deepseek-reasoner` 对应 DeepSeek-V3.2 的**思考模式**。

### 首次调用样例

```python
from openai import OpenAI

client = OpenAI(
    api_key="<DeepSeek API Key>",
    base_url="https://api.deepseek.com"
)

response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"}
    ],
    stream=False
)

print(response.choices[0].message.content)
```

> 将 `stream` 设置为 `True` 可使用流式输出。

---

## 2. API 端点参考

### 2.1 对话补全 (/chat/completions)

**POST** `/chat/completions`

根据输入的上下文，让模型补全对话内容。

#### Request Body（application/json）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| messages | object[] | ✅ | 对话消息列表（>=1），包含 System / User / Assistant / Tool message |
| model | string | ✅ | 模型 ID：`deepseek-chat` 或 `deepseek-reasoner` |
| thinking | object | ❌ | 控制思考模式切换。`{"type": "enabled"}` 开启，`{"type": "disabled"}` 关闭 |
| frequency_penalty | number | ❌ | -2.0 ~ 2.0，默认 0。正值降低重复 |
| max_tokens | integer | ❌ | 单次生成最大 token 数（含思维链）。默认 32K，最大 64K |
| presence_penalty | number | ❌ | -2.0 ~ 2.0，默认 0。正值增加新话题 |
| response_format | object | ❌ | `{"type": "json_object"}` 启用 JSON 模式 |
| stop | string / string[] | ❌ | 最多 16 个停止词 |
| stream | boolean | ❌ | 是否流式输出 |
| stream_options | object | ❌ | 流式选项。`include_usage: true` 在末尾返回 usage 块 |
| temperature | number | ❌ | 0 ~ 2，默认 1 |
| top_p | number | ❌ | <=1，默认 1 |
| tools | object[] | ❌ | 工具列表，最多 128 个 function |
| tool_choice | string/object | ❌ | `none` / `auto` / `required` / 指定 function |
| logprobs | boolean | ❌ | 是否返回输出 token 对数概率 |
| top_logprobs | integer | ❌ | 0 ~ 20，需 logprobs=true |

#### Response（成功 200）

```
{
  "id": "string",
  "choices": [
    {
      "finish_reason": "stop | length | content_filter | tool_calls | insufficient_system_resource",
      "index": 0,
      "message": {
        "content": "string | null",
        "reasoning_content": "string | null (仅 deepseek-reasoner)",
        "tool_calls": [...],
        "role": "assistant"
      },
      "logprobs": { ... }
    }
  ],
  "created": 1234567890,
  "model": "deepseek-chat",
  "system_fingerprint": "string",
  "object": "chat.completion",
  "usage": {
    "completion_tokens": 100,
    "prompt_tokens": 50,
    "prompt_cache_hit_tokens": 30,
    "prompt_cache_miss_tokens": 20,
    "total_tokens": 150,
    "completion_tokens_details": {
      "reasoning_tokens": 0
    }
  }
}
```

**finish_reason 说明：**
- `stop`：模型自然停止，或遇到 stop 序列
- `length`：达到 max_tokens 或上下文长度限制
- `content_filter`：触发内容过滤
- `tool_calls`：模型调用了工具
- `insufficient_system_resource`：系统推理资源不足

---

### 2.2 FIM 补全（Beta）

**POST** `/completions`

FIM（Fill-In-the-Middle）补全 API。用户需设置 `base_url="https://api.deepseek.com/beta"`。

#### Request Body

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| model | string | ✅ | `deepseek-chat` |
| prompt | string | ✅ | 前缀内容 |
| suffix | string | ❌ | 后缀内容 |
| max_tokens | integer | ❌ | 最大生成 token 数（上限 4K） |
| temperature | number | ❌ | 0 ~ 2，默认 1 |
| top_p | number | ❌ | <=1，默认 1 |
| frequency_penalty | number | ❌ | -2 ~ 2，默认 0 |
| presence_penalty | number | ❌ | -2 ~ 2，默认 0 |
| stop | string / string[] | ❌ | 停止词 |
| stream | boolean | ❌ | 流式输出 |
| echo | boolean | ❌ | 是否在输出中包含 prompt |
| logprobs | integer | ❌ | 0 ~ 20 |

#### Response

```
{
  "id": "string",
  "choices": [
    {
      "finish_reason": "stop | length | content_filter | insufficient_system_resource",
      "index": 0,
      "logprobs": { ... },
      "text": "补全的文本"
    }
  ],
  "created": 1234567890,
  "model": "deepseek-chat",
  "object": "text_completion",
  "usage": {
    "completion_tokens": 50,
    "prompt_tokens": 30,
    "prompt_cache_hit_tokens": 10,
    "prompt_cache_miss_tokens": 20,
    "total_tokens": 80
  }
}
```

#### Python 样例

```python
from openai import OpenAI

client = OpenAI(
    api_key="<your api key>",
    base_url="https://api.deepseek.com/beta",
)

response = client.completions.create(
    model="deepseek-chat",
    prompt="def fib(a):",
    suffix="    return fib(a-1) + fib(a-2)",
    max_tokens=128
)
print(response.choices[0].text)
```

---

### 2.3 列出模型

**GET** `/models`

列出可用的模型列表。

#### Response

```
{
  "object": "list",
  "data": [
    {
      "id": "deepseek-chat",
      "object": "model",
      "owned_by": "deepseek"
    }
  ]
}
```

---

### 2.4 查询余额

**GET** `/user/balance`

#### Response

```
[
  {
    "currency": "CNY | USD",
    "total_balance": "string",
    "granted_balance": "string",
    "topped_up_balance": "string"
  }
]
```

- `currency`：货币，CNY 或 USD
- `total_balance`：总可用余额（含赠金和充值）
- `granted_balance`：未过期的赠金余额
- `topped_up_balance`：充值余额

---

## 3. 功能指南

### 3.1 思考模式

DeepSeek 模型支持思考模式：在输出最终回答之前，模型会先输出一段思维链内容，以提升最终答案的准确性。

**开启方式（二选一）：**
1. 设置 model：`"model": "deepseek-reasoner"`
2. 设置 thinking 参数：`"thinking": {"type": "enabled"}`

使用 OpenAI SDK 时，thinking 参数需传入 `extra_body`：

```python
response = client.chat.completions.create(
    model="deepseek-chat",
    # ...
    extra_body={"thinking": {"type": "enabled"}}
)
```

**输出字段：**
- `reasoning_content`：思维链内容
- `content`：最终回答内容
- `tool_calls`：模型工具调用

**支持的功能：** JSON Output、Tool Calls、对话补全、对话前缀续写

**不支持的功能：** FIM 补全

**不支持的参数：** `temperature`、`top_p`、`presence_penalty`、`frequency_penalty`、`logprobs`、`top_logprobs`（设置前四个不会报错但不生效，设置后两个会报错）

#### 多轮对话拼接

在每一轮对话中，模型输出 `reasoning_content` 和 `content`。下一轮对话时，**之前轮的 reasoning_content 不会被拼接进上下文**。

#### 非流式样例

```python
from openai import OpenAI

client = OpenAI(api_key="<DeepSeek API Key>", base_url="https://api.deepseek.com")

# Turn 1
messages = [{"role": "user", "content": "9.11 and 9.8, which is greater?"}]
response = client.chat.completions.create(
    model="deepseek-reasoner",
    messages=messages
)

reasoning_content = response.choices[0].message.reasoning_content
content = response.choices[0].message.content

# Turn 2 — 只传入上一轮的 content，不传 reasoning_content
messages.append({'role': 'assistant', 'content': content})
messages.append({'role': 'user', 'content': "How many Rs are there in the word 'strawberry'?"})
response = client.chat.completions.create(
    model="deepseek-reasoner",
    messages=messages
)
```

#### 思考模式下的工具调用

在回答过程中，模型可进行多轮思考 + 工具调用。关键点：
- 同一个 Turn 内的子请求，需回传 `reasoning_content` 给 API
- 新 Turn 开始时，建议清除之前 Turn 的 `reasoning_content` 以节省带宽

```python
import os
import json
from openai import OpenAI

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_date",
            "description": "Get the current date",
            "parameters": {"type": "object", "properties": {}},
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather of a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "The city name"},
                    "date": {"type": "string", "description": "The date in format YYYY-mm-dd"},
                },
                "required": ["location", "date"]
            },
        }
    },
]

def get_date_mock():
    return "2025-12-01"

def get_weather_mock(location, date):
    return "Cloudy 7~13°C"

TOOL_CALL_MAP = {
    "get_date": get_date_mock,
    "get_weather": get_weather_mock
}

def clear_reasoning_content(messages):
    for message in messages:
        if hasattr(message, 'reasoning_content'):
            message.reasoning_content = None

def run_turn(turn, messages):
    sub_turn = 1
    while True:
        response = client.chat.completions.create(
            model='deepseek-chat',
            messages=messages,
            tools=tools,
            extra_body={"thinking": {"type": "enabled"}}
        )
        messages.append(response.choices[0].message)
        reasoning_content = response.choices[0].message.reasoning_content
        content = response.choices[0].message.content
        tool_calls = response.choices[0].message.tool_calls
        print(f"Turn {turn}.{sub_turn}\n{reasoning_content=}\n{content=}\n{tool_calls=}")
        if tool_calls is None:
            break
        for tool in tool_calls:
            tool_function = TOOL_CALL_MAP[tool.function.name]
            tool_result = tool_function(**json.loads(tool.function.arguments))
            print(f"tool result for {tool.function.name}: {tool_result}\n")
            messages.append({
                "role": "tool",
                "tool_call_id": tool.id,
                "content": tool_result,
            })
        sub_turn += 1

client = OpenAI(
    api_key=os.environ.get('DEEPSEEK_API_KEY'),
    base_url="https://api.deepseek.com",
)

# Turn 1
messages = [{"role": "user", "content": "How's the weather in Hangzhou Tomorrow"}]
run_turn(1, messages)

# Turn 2 — 清除旧的 reasoning_content
messages.append({"role": "user", "content": "How's the weather in Hangzhou Tomorrow"})
clear_reasoning_content(messages)
run_turn(2, messages)
```

---

### 3.2 多轮对话

DeepSeek `/chat/completions` API 是**无状态** API，服务端不记录用户请求的上下文。用户在每次请求时，需将之前所有对话历史拼接好后传递给 API。

```python
from openai import OpenAI

client = OpenAI(api_key="<DeepSeek API Key>", base_url="https://api.deepseek.com")

# Round 1
messages = [{"role": "user", "content": "What's the highest mountain in the world?"}]
response = client.chat.completions.create(
    model="deepseek-chat",
    messages=messages
)

messages.append(response.choices[0].message)
print(f"Messages Round 1: {messages}")

# Round 2
messages.append({"role": "user", "content": "What is the second?"})
response = client.chat.completions.create(
    model="deepseek-chat",
    messages=messages
)

messages.append(response.choices[0].message)
print(f"Messages Round 2: {messages}")
```

---

### 3.3 对话前缀续写（Beta）

通过设置 assistant 消息的 `prefix: True`，可以强制模型以前缀内容开头续写。需设置 `base_url="https://api.deepseek.com/beta"`。

```python
from openai import OpenAI

client = OpenAI(
    api_key="<your api key>",
    base_url="https://api.deepseek.com/beta",
)

messages = [
    {"role": "user", "content": "Please write quick sort code"},
    {"role": "assistant", "content": "```python\n", "prefix": True}
]
response = client.chat.completions.create(
    model="deepseek-chat",
    messages=messages,
    stop=["```"],
)
print(response.choices[0].message.content)
```

> 上例通过设置 `stop=['```']` 避免模型输出额外解释。

---

### 3.4 FIM 补全指南

在 FIM（Fill In the Middle）补全中，用户可以提供前缀和后缀（可选），模型补全中间内容。常用于代码补全等场景。

**注意事项：**
- 最大补全长度为 4K
- 需设置 `base_url="https://api.deepseek.com/beta"`

```python
from openai import OpenAI

client = OpenAI(
    api_key="<your api key>",
    base_url="https://api.deepseek.com/beta",
)

response = client.completions.create(
    model="deepseek-chat",
    prompt="def fib(a):",
    suffix="    return fib(a-1) + fib(a-2)",
    max_tokens=128
)
print(response.choices[0].text)
```

> [Continue](https://continue.dev) 是一款支持代码补全的 VSCode 插件，可参考 [DeepSeek 集成文档](https://github.com/deepseek-ai/awesome-deepseek-integration/blob/main/docs/continue/README_cn.md) 配置。

---

### 3.5 JSON Output

通过设置 `response_format={'type': 'json_object'}`，强制模型输出合法 JSON。**必须在 prompt 中指示模型生成 JSON，否则模型可能生成空白直到 token 耗尽。**

```python
import json
from openai import OpenAI

client = OpenAI(
    api_key="<your api key>",
    base_url="https://api.deepseek.com",
)

system_prompt = """
The user will provide some exam text. Please parse the "question" and "answer" and output them in JSON format.

EXAMPLE INPUT:
Which is the highest mountain in the world? Mount Everest.

EXAMPLE JSON OUTPUT:
{
    "question": "Which is the highest mountain in the world?",
    "answer": "Mount Everest"
}
"""

user_prompt = "Which is the longest river in the world? The Nile River."

messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_prompt}
]

response = client.chat.completions.create(
    model="deepseek-chat",
    messages=messages,
    response_format={'type': 'json_object'}
)

print(json.loads(response.choices[0].message.content))
```

---

### 3.6 Tool Calls

Tool Calls 让模型能够调用外部工具来增强自身能力。

#### 非思考模式样例

```python
from openai import OpenAI

def send_messages(messages):
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=messages,
        tools=tools
    )
    return response.choices[0].message

client = OpenAI(
    api_key="<your api key>",
    base_url="https://api.deepseek.com",
)

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather of a location, the user should supply a location first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city and state, e.g. San Francisco, CA",
                    }
                },
                "required": ["location"]
            },
        }
    },
]

messages = [{"role": "user", "content": "How's the weather in Hangzhou, Zhejiang?"}]
message = send_messages(messages)
print(f"User>\t {messages[0]['content']}")

tool = message.tool_calls[0]
messages.append(message)

# 用户端执行实际函数后，将结果传回
messages.append({"role": "tool", "tool_call_id": tool.id, "content": "24℃"})
message = send_messages(messages)
print(f"Model>\t {message.content}")
```

> 注：`get_weather` 函数功能需由用户提供，模型本身不执行具体函数。

#### strict 模式（Beta）

strict 模式下，模型输出 Function 调用时严格遵循 JSON Schema 格式。需设置 `base_url="https://api.deepseek.com/beta"`，且所有 function 设置 `strict: true`。

```json
{
  "type": "function",
  "function": {
    "name": "get_weather",
    "strict": true,
    "description": "Get weather of a location",
    "parameters": {
      "type": "object",
      "properties": {
        "location": {
          "type": "string",
          "description": "The city and state"
        }
      },
      "required": ["location"],
      "additionalProperties": false
    }
  }
}
```

**strict 模式支持的 JSON Schema 类型：**

| 类型 | 支持的约束 |
|------|-----------|
| object | properties, required, additionalProperties=false |
| string | pattern（正则）, format（email/hostname/ipv4/ipv6/uuid） |
| number/integer | const, default, minimum, maximum, exclusiveMinimum, exclusiveMaximum, multipleOf |
| array | items（不支持 minItems/maxItems） |
| enum | 限定输出值范围 |
| anyOf | 多 schema 匹配 |
| $ref / $def | 模块化引用，支持递归结构 |

---

### 3.7 上下文硬盘缓存

DeepSeek API 上下文硬盘缓存技术**对所有用户默认开启**，用户无需修改代码。

**核心原理：** 后续请求与之前请求存在前缀重复时，重复部分从缓存拉取，计入"缓存命中"。

**关键规则：**
- 只有**重复的前缀部分**才能触发缓存命中
- 缓存系统以 64 tokens 为存储单元，不足 64 tokens 不缓存
- "尽力而为"，不保证 100% 命中
- 缓存构建耗时秒级，不再使用后自动清空（几小时到几天）

**查看缓存命中情况（usage 字段）：**
- `prompt_cache_hit_tokens`：缓存命中的 tokens 数（0.1 元 / 百万 tokens）
- `prompt_cache_miss_tokens`：缓存未命中的 tokens 数（1 元 / 百万 tokens）

**典型场景：**

1. **长文本问答** — 相同 system + 文档前缀 → 命中
2. **多轮对话** — 历史消息前缀一致 → 命中
3. **Few-shot 学习** — 相同示例上下文 → 命中，费用显著降低

> 硬盘缓存只匹配输入前缀，输出仍受 temperature 等参数影响，效果与不使用缓存相同。

---

### 3.8 Anthropic API 兼容

DeepSeek API 新增对 Anthropic API 格式的支持，可将 DeepSeek 接入 Anthropic API 生态。

#### 接入 Claude Code

```bash
npm install -g @anthropic-ai/claude-code

export ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
export ANTHROPIC_AUTH_TOKEN=${DEEPSEEK_API_KEY}
export API_TIMEOUT_MS=600000
export ANTHROPIC_MODEL=deepseek-chat
export ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-chat
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

cd my-project
claude
```

#### 通过 Anthropic SDK 调用

```bash
pip install anthropic

export ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
export ANTHROPIC_API_KEY=${YOUR_API_KEY}
```

```python
import anthropic

client = anthropic.Anthropic()

message = client.messages.create(
    model="deepseek-chat",
    max_tokens=1000,
    system="You are a helpful assistant.",
    messages=[
        {
            "role": "user",
            "content": [{"type": "text", "text": "Hi, how are you?"}]
        }
    ]
)
print(message.content)
```

> 传入不支持的模型名时，API 后端自动映射到 `deepseek-chat`。

#### Anthropic API 兼容性详情

**Header 字段：**

| 字段 | 支持状态 |
|------|---------|
| anthropic-beta | 忽略 |
| anthropic-version | 忽略 |
| x-api-key | 完全支持 |

**Simple Fields：**

| 字段 | 支持状态 |
|------|---------|
| model | 使用 DeepSeek 模型替代 |
| max_tokens | 完全支持 |
| stop_sequences | 完全支持 |
| stream | 完全支持 |
| system | 完全支持 |
| temperature | 完全支持（范围 0.0 ~ 2.0） |
| thinking | 支持（budget_tokens 忽略） |
| top_p | 完全支持 |
| top_k | 忽略 |
| container / mcp_servers / metadata / service_tier | 忽略 |

**Tool Fields：**

| 字段 | 支持状态 |
|------|---------|
| tools[].name | 完全支持 |
| tools[].input_schema | 完全支持 |
| tools[].description | 完全支持 |
| tools[].cache_control | 忽略 |
| tool_choice: none | 完全支持 |
| tool_choice: auto/any/tool | 支持（disable_parallel_tool_use 忽略） |

**Message Fields：**

| content 类型 | 支持状态 |
|-------------|---------|
| string | 完全支持 |
| text | 完全支持 |
| thinking | 支持 |
| tool_use (id, input, name) | 完全支持 |
| tool_result (tool_use_id, content) | 完全支持 |
| image / document / search_result / redacted_thinking | 不支持 |
| server_tool_use / mcp_tool_use / mcp_tool_result 等 | 不支持 |

---

## 4. 参数与限制

### 4.1 Temperature 设置

`temperature` 参数默认为 1.0。建议按场景设置：

| 场景 | 推荐温度 |
|------|---------|
| 代码生成 / 数学解题 | 0.0 |
| 数据抽取 / 分析 | 1.0 |
| 通用对话 | 1.3 |
| 翻译 | 1.3 |
| 创意类写作 / 诗歌创作 | 1.5 |

---

### 4.2 限速

DeepSeek API **不限制用户并发量**，会尽力保证所有请求的服务质量。

高流量时，请求发出后可能需等待。等待期间：
- **非流式请求**：持续返回空行
- **流式请求**：持续返回 SSE keep-alive 注释（`: keep-alive`）

这些内容不影响 OpenAI SDK 对 JSON body 的解析。如果自行解析 HTTP 响应，需注意处理空行或注释。

**10 分钟后**若请求仍未开始推理，服务器将关闭连接。

---

### 4.3 错误码

| 错误码 | 描述 | 原因 & 解决方法 |
|--------|------|----------------|
| 400 | 格式错误 | 请求体格式错误。请根据错误信息修改请求体 |
| 401 | 认证失败 | API key 错误。请检查 API key，或前往 [创建](https://platform.deepseek.com/api_keys) |
| 402 | 余额不足 | 账号余额不足。请前往 [充值](https://platform.deepseek.com/top_up) |
| 422 | 参数错误 | 请求体参数错误。请根据错误信息修改参数 |
| 429 | 请求速率达到上限 | TPM 或 RPM 达到上限。请合理规划请求速率 |
| 500 | 服务器故障 | 服务器内部故障。请等待后重试，若持续请联系客服 |
| 503 | 服务器繁忙 | 服务器负载过高。请稍后重试 |

---

> 文档来源：https://api-docs.deepseek.com/zh-cn/
