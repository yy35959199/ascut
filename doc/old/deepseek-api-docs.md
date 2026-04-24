# DeepSeek API 完整文档

> 来源：https://api-docs.deepseek.com/zh-cn/
> 整理日期：2026-04-24
> 代码样例统一选用 Python（OpenAI SDK 兼容格式，除特别说明外）

---

## 目录

1. [快速开始](#1-快速开始)
   - 1.1 [接入信息](#11-接入信息)
   - 1.2 [模型说明](#12-模型说明)
   - 1.3 [首次调用样例](#13-首次调用样例)
2. [模型 & 价格](#2-模型--价格)
3. [API 端点参考](#3-api-端点参考)
   - 3.1 [对话补全 (/chat/completions)](#31-对话补全-chatcompletions)
   - 3.2 [FIM 补全 Beta (/completions)](#32-fim-补全beta)
   - 3.3 [列出模型 (/models)](#33-列出模型)
   - 3.4 [查询余额 (/user/balance)](#34-查询余额)
4. [功能指南](#4-功能指南)
   - 4.1 [思考模式](#41-思考模式)
   - 4.2 [多轮对话](#42-多轮对话)
   - 4.3 [对话前缀续写 Beta](#43-对话前缀续写beta)
   - 4.4 [FIM 补全指南](#44-fim-补全指南)
   - 4.5 [JSON Output](#45-json-output)
   - 4.6 [Tool Calls](#46-tool-calls)
   - 4.7 [上下文硬盘缓存](#47-上下文硬盘缓存)
   - 4.8 [Anthropic API 兼容](#48-anthropic-api-兼容)
5. [参数与限制](#5-参数与限制)
   - 5.1 [Temperature 设置](#51-temperature-设置)
   - 5.2 [限速](#52-限速)
   - 5.3 [错误码](#53-错误码)

---

## 1. 快速开始

DeepSeek API 使用与 OpenAI / Anthropic 兼容的 API 格式，通过修改配置，您可以使用 OpenAI SDK 或 Anthropic SDK 来访问 DeepSeek API。

### 1.1 接入信息

| 参数 | OpenAI 格式 | Anthropic 格式 |
|------|------------|---------------|
| base_url | `https://api.deepseek.com` | `https://api.deepseek.com/anthropic` |
| api_key | 前往 [API Keys](https://platform.deepseek.com/api_keys) 申请 | 同左 |

> 出于与 OpenAI 兼容考虑，也可将 base_url 设置为 `https://api.deepseek.com/v1`，此处 v1 与模型版本无关。

### 1.2 模型说明

| 模型名 | 对应模型 | 说明 |
|--------|---------|------|
| `deepseek-v4-flash` | DeepSeek-V4-Flash | 支持非思考与思考模式（默认思考模式） |
| `deepseek-v4-pro` | DeepSeek-V4-Pro | 支持非思考与思考模式（默认思考模式） |
| `deepseek-chat` | ⚠️ 即将于 2026/07/24 弃用 | 兼容映射到 deepseek-v4-flash 的非思考模式 |
| `deepseek-reasoner` | ⚠️ 即将于 2026/07/24 弃用 | 兼容映射到 deepseek-v4-flash 的思考模式 |

- **上下文长度**：1M tokens
- **最大输出长度**：384K tokens
- **思考模式默认开启**，可通过 `thinking` 参数切换

### 1.3 首次调用样例

```python
from openai import OpenAI

client = OpenAI(
    api_key="<DeepSeek API Key>",
    base_url="https://api.deepseek.com"
)

response = client.chat.completions.create(
    model="deepseek-v4-pro",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"}
    ],
    thinking={"type": "enabled"},
    reasoning_effort="high",
    stream=False
)

print(response.choices[0].message.content)
```

> 将 `stream` 设置为 `True` 可使用流式输出。Anthropic API 格式的访问样例见 [Anthropic API 兼容](#48-anthropic-api-兼容)。

---

## 2. 模型 & 价格

下表所列价格以"百万 tokens"为单位。Token 是模型用来表示自然语言文本的最小单位，可以是一个词、一个数字或一个标点符号。根据模型输入和输出的总 token 数计量计费。

### 模型细节

| | deepseek-v4-flash | deepseek-v4-pro |
|---|---|---|
| 模型版本 | DeepSeek-V4-Flash | DeepSeek-V4-Pro |
| BASE URL（OpenAI） | `https://api.deepseek.com` | `https://api.deepseek.com` |
| BASE URL（Anthropic） | `https://api.deepseek.com/anthropic` | `https://api.deepseek.com/anthropic` |
| 思考模式 | 支持非思考与思考模式（默认） | 支持非思考与思考模式（默认） |
| 上下文长度 | 1M | 1M |
| 最大输出长度 | 384K | 384K |
| JSON Output | ✅ | ✅ |
| Tool Calls | ✅ | ✅ |
| 对话前缀续写（Beta） | ✅ | ✅ |
| FIM 补全（Beta） | 仅非思考模式 | 仅非思考模式 |

### 价格（元 / 百万 tokens）

| | deepseek-v4-flash | deepseek-v4-pro |
|---|---|---|
| 输入（缓存命中） | 0.2 | 1 |
| 输入（缓存未命中） | 1 | 12 |
| 输出 | 2 | 24 |

**扣费规则：** 扣减费用 = token 消耗量 × 模型单价。费用从充值余额或赠送余额中扣减，优先扣减赠送余额。

---

## 3. API 端点参考

### 3.1 对话补全 (/chat/completions)

**POST** `/chat/completions`

根据输入的上下文，让模型补全对话内容。

#### Request Body（application/json）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| messages | object[] | ✅ | 对话消息列表（>=1）。支持 System / User / Assistant / Tool message |
| model | string | ✅ | 模型 ID：`deepseek-v4-flash` 或 `deepseek-v4-pro` |
| thinking | object | ❌ | 思考模式开关。`{"type": "enabled"}` 开启（默认），`{"type": "disabled"}` 关闭。可选 |
| reasoning_effort | string | ❌ | 推理强度：`high`（默认）或 `max`。对复杂 Agent 类请求（如 Claude Code、OpenCode）自动设为 max。出于兼容，low/medium → high，xhigh → max |
| frequency_penalty | number | ❌ | -2.0 ~ 2.0，默认 0。正值降低重复 |
| max_tokens | integer | ❌ | 单次生成最大 token 数。输入+输出总长度受上下文长度限制 |
| presence_penalty | number | ❌ | -2.0 ~ 2.0，默认 0。正值增加新话题 |
| response_format | object | ❌ | `{"type": "json_object"}` 启用 JSON 模式，`{"type": "text"}`（默认） |
| stop | string / string[] | ❌ | 最多 16 个停止词 |
| stream | boolean | ❌ | 流式输出（SSE），以 `data: [DONE]` 结尾 |
| stream_options | object | ❌ | 流式选项。`{"include_usage": true}` 在 `data: [DONE]` 前返回 usage 块 |
| temperature | number | ❌ | 0 ~ 2，默认 1。思考模式下设置不生效 |
| top_p | number | ❌ | <=1，默认 1。思考模式下设置不生效 |
| tools | object[] | ❌ | 工具列表，最多 128 个 function |
| tool_choice | string/object | ❌ | `none` / `auto`（默认，有 tools 时）/ `required` / `{"type":"function","function":{"name":"xxx"}}` |
| logprobs | boolean | ❌ | 是否返回输出 token 对数概率 |
| top_logprobs | integer | ❌ | 0 ~ 20，需 logprobs=true |

#### messages 字段详解

**System message：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| content | string | ✅ | 系统消息内容 |
| role | string | ✅ | 固定值 `system` |
| name | string | ❌ | 参与者名称，用于区分同角色的参与者 |

**User message：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| content | string | ✅ | 用户消息内容 |
| role | string | ✅ | 固定值 `user` |
| name | string | ❌ | 参与者名称 |

**Assistant message：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| content | string | ✅ | 助手回复内容 |
| role | string | ✅ | 固定值 `assistant` |
| reasoning_content | string | ❌ | 思维链内容（思考模式下）。非工具调用轮次无需回传；工具调用轮次必须回传 |
| tool_calls | object[] | ❌ | 模型生成的工具调用列表 |

**Tool message：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| role | string | ✅ | 固定值 `tool` |
| tool_call_id | string | ✅ | 对应的工具调用 ID |
| content | string | ✅ | 工具返回结果 |

#### tools 字段详解

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| type | string | ✅ | 固定值 `function` |
| function.name | string | ✅ | 函数名，a-z/A-Z/0-9/下划线/连字符，最长 64 字符 |
| function.description | string | ❌ | 函数描述，供模型理解何时/如何调用 |
| function.parameters | object | ❌ | JSON Schema 格式的参数定义 |
| function.strict | boolean | ❌ | Beta。设为 true 启用 strict 模式，确保输出符合 JSON Schema |

#### Response（成功 200）

```json
{
  "id": "string",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "deepseek-v4-pro",
  "system_fingerprint": "string",
  "choices": [
    {
      "index": 0,
      "finish_reason": "stop | length | content_filter | tool_calls | insufficient_system_resource",
      "message": {
        "role": "assistant",
        "content": "string | null",
        "reasoning_content": "string | null",
        "tool_calls": [
          {
            "id": "string",
            "type": "function",
            "function": {
              "name": "string",
              "arguments": "string (JSON)"
            }
          }
        ]
      },
      "logprobs": {
        "content": [
          {
            "token": "string",
            "logprob": -0.5,
            "bytes": [228, 186],
            "top_logprobs": [
              {"token": "string", "logprob": -0.5, "bytes": [228, 186]}
            ]
          }
        ],
        "reasoning_content": [
          {
            "token": "string",
            "logprob": -0.5,
            "bytes": [228, 186],
            "top_logprobs": [
              {"token": "string", "logprob": -0.5, "bytes": [228, 186]}
            ]
          }
        ]
      }
    }
  ],
  "usage": {
    "prompt_tokens": 50,
    "prompt_cache_hit_tokens": 30,
    "prompt_cache_miss_tokens": 20,
    "completion_tokens": 100,
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
- `insufficient_system_resource`：系统推理资源不足，生成被打断

---

### 3.2 FIM 补全（Beta）

**POST** `/completions`

FIM（Fill-In-the-Middle）补全 API。需设置 `base_url="https://api.deepseek.com/beta"`。

#### Request Body

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| model | string | ✅ | `deepseek-v4-pro`（仅非思考模式） |
| prompt | string | ✅ | 前缀内容 |
| suffix | string | ❌ | 后缀内容 |
| max_tokens | integer | ❌ | 最大生成 token 数（上限 4K） |
| temperature | number | ❌ | 0 ~ 2，默认 1 |
| top_p | number | ❌ | <=1，默认 1 |
| frequency_penalty | number | ❌ | -2 ~ 2，默认 0 |
| presence_penalty | number | ❌ | -2 ~ 2，默认 0 |
| stop | string / string[] | ❌ | 停止词 |
| stream | boolean | ❌ | 流式输出 |
| stream_options | object | ❌ | 流式选项。`include_usage: true` |
| echo | boolean | ❌ | 是否在输出中包含 prompt |
| logprobs | integer | ❌ | 0 ~ 20 |

#### Response

```json
{
  "id": "string",
  "object": "text_completion",
  "created": 1234567890,
  "model": "deepseek-v4-pro",
  "system_fingerprint": "string",
  "choices": [
    {
      "index": 0,
      "finish_reason": "stop | length | content_filter | insufficient_system_resource",
      "text": "补全的文本",
      "logprobs": {
        "text_offset": [0, 5, 10],
        "token_logprobs": [-0.5, -0.3, -0.8],
        "tokens": ["token1", "token2", "token3"],
        "top_logprobs": [{}]
      }
    }
  ],
  "usage": {
    "prompt_tokens": 30,
    "prompt_cache_hit_tokens": 10,
    "prompt_cache_miss_tokens": 20,
    "completion_tokens": 50,
    "total_tokens": 80,
    "completion_tokens_details": {
      "reasoning_tokens": 0
    }
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
    model="deepseek-v4-pro",
    prompt="def fib(a):",
    suffix="    return fib(a-1) + fib(a-2)",
    max_tokens=128
)
print(response.choices[0].text)
```

---

### 3.3 列出模型

**GET** `/models`

列出可用的模型列表，并提供相关模型的基本信息。

#### Response

```json
{
  "object": "list",
  "data": [
    {
      "id": "deepseek-v4-pro",
      "object": "model",
      "owned_by": "deepseek"
    },
    {
      "id": "deepseek-v4-flash",
      "object": "model",
      "owned_by": "deepseek"
    }
  ]
}
```

---

### 3.4 查询余额

**GET** `/user/balance`

#### Response

```json
[
  {
    "currency": "CNY",
    "total_balance": "string",
    "granted_balance": "string",
    "topped_up_balance": "string"
  }
]
```

| 字段 | 说明 |
|------|------|
| currency | 货币：`CNY`（人民币）或 `USD`（美元） |
| total_balance | 总可用余额（含赠金和充值） |
| granted_balance | 未过期的赠金余额 |
| topped_up_balance | 充值余额 |

---

## 4. 功能指南

### 4.1 思考模式

DeepSeek 模型支持思考模式：在输出最终回答之前，模型会先输出一段思维链内容（reasoning_content），以提升最终答案的准确性。

#### 思考模式开关与思考强度

| 控制项 | OpenAI 格式 | Anthropic 格式 |
|--------|------------|---------------|
| 思考模式开关 | `{"thinking": {"type": "enabled/disabled"}}` | 同 |
| 思考强度控制 | `{"reasoning_effort": "high/max"}` | `{"output_config": {"effort": "high/max"}}` |

- **默认**：思考模式开启，effort 为 `high`
- 对复杂 Agent 类请求（如 Claude Code、OpenCode），effort 自动设为 `max`
- 出于兼容，low/medium → high，xhigh → max

使用 OpenAI SDK 时，`thinking` 参数需传入 `extra_body`：

```python
response = client.chat.completions.create(
    model="deepseek-v4-pro",
    messages=messages,
    reasoning_effort="high",
    extra_body={"thinking": {"type": "enabled"}}
)
```

#### 输入输出参数

- **不支持的参数**：`temperature`、`top_p`、`presence_penalty`、`frequency_penalty`（设置不会报错但不生效）
- **logprobs / top_logprobs**：思考模式下设置会报错
- **输出字段**：`reasoning_content`（思维链）与 `content`（最终回答）同级返回

#### 多轮对话拼接规则

- 无工具调用的轮次：之前轮的 `reasoning_content` **无需**参与上下文拼接，传入 API 会被忽略
- 有工具调用的轮次：`reasoning_content` **必须**在后续所有 user 交互轮次中回传给 API

#### 非流式样例

```python
from openai import OpenAI

client = OpenAI(api_key="<DeepSeek API Key>", base_url="https://api.deepseek.com")

# Turn 1
messages = [{"role": "user", "content": "9.11 and 9.8, which is greater?"}]
response = client.chat.completions.create(
    model="deepseek-v4-pro",
    messages=messages,
    reasoning_effort="high",
    extra_body={"thinking": {"type": "enabled"}},
)

reasoning_content = response.choices[0].message.reasoning_content
content = response.choices[0].message.content

# Turn 2 — reasoning_content 会被 API 忽略，无需特别处理
messages.append(response.choices[0].message)
messages.append({"role": "user", "content": "How many Rs are there in the word 'strawberry'?"})
response = client.chat.completions.create(
    model="deepseek-v4-pro",
    messages=messages,
    reasoning_effort="high",
    extra_body={"thinking": {"type": "enabled"}},
)
```

#### 思考模式下的工具调用样例

```python
import os
import json
from openai import OpenAI
from datetime import datetime

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
            "description": "Get weather of a location, the user should supply the location and date.",
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
    return datetime.now().strftime("%Y-%m-%d")

def get_weather_mock(location, date):
    return "Cloudy 7~13°C"

TOOL_CALL_MAP = {
    "get_date": get_date_mock,
    "get_weather": get_weather_mock
}

client = OpenAI(
    api_key=os.environ.get('DEEPSEEK_API_KEY'),
    base_url="https://api.deepseek.com",
)

def run_turn(turn, messages):
    sub_turn = 1
    while True:
        response = client.chat.completions.create(
            model='deepseek-v4-pro',
            messages=messages,
            tools=tools,
            reasoning_effort="high",
            extra_body={"thinking": {"type": "enabled"}},
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
    print()

# Turn 1
turn = 1
messages = [{"role": "user", "content": "How's the weather in Hangzhou Tomorrow"}]
run_turn(turn, messages)

# Turn 2 — 仍携带 Turn1 的 reasoning_content 给 API
turn = 2
messages.append({"role": "user", "content": "How's the weather in Guangzhou Tomorrow"})
run_turn(turn, messages)
```

> `messages.append(response.choices[0].message)` 等价于同时 append content、reasoning_content、tool_calls。

---

### 4.2 多轮对话

DeepSeek `/chat/completions` API 是**无状态** API，服务端不记录用户请求的上下文。用户在每次请求时，需将之前所有对话历史拼接好后传递给 API。

```python
from openai import OpenAI

client = OpenAI(api_key="<DeepSeek API Key>", base_url="https://api.deepseek.com")

# Round 1
messages = [{"role": "user", "content": "What's the highest mountain in the world?"}]
response = client.chat.completions.create(
    model="deepseek-v4-pro",
    messages=messages
)
messages.append(response.choices[0].message)
print(f"Messages Round 1: {messages}")

# Round 2
messages.append({"role": "user", "content": "What is the second?"})
response = client.chat.completions.create(
    model="deepseek-v4-pro",
    messages=messages
)
messages.append(response.choices[0].message)
print(f"Messages Round 2: {messages}")
```

---

### 4.3 对话前缀续写（Beta）

通过设置 assistant 消息的 `"prefix": True`，可以强制模型以前缀内容开头续写。需设置 `base_url="https://api.deepseek.com/beta"`。

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
    model="deepseek-v4-pro",
    messages=messages,
    stop=["```"],
)
print(response.choices[0].message.content)
```

> 上例通过设置 `stop=['```']` 避免模型输出额外解释。

---

### 4.4 FIM 补全指南

在 FIM（Fill In the Middle）补全中，用户可以提供前缀和后缀（可选），模型补全中间内容。常用于代码补全等场景。

**注意事项：**
- 最大补全长度为 4K
- 需设置 `base_url="https://api.deepseek.com/beta"`
- 仅非思考模式支持

```python
from openai import OpenAI

client = OpenAI(
    api_key="<your api key>",
    base_url="https://api.deepseek.com/beta",
)

response = client.completions.create(
    model="deepseek-v4-pro",
    prompt="def fib(a):",
    suffix="    return fib(a-1) + fib(a-2)",
    max_tokens=128
)
print(response.choices[0].text)
```

> [Continue](https://continue.dev) 是一款支持代码补全的 VSCode 插件，可参考 [DeepSeek 集成文档](https://github.com/deepseek-ai/awesome-deepseek-integration/blob/main/docs/continue/README_cn.md) 配置。

---

### 4.5 JSON Output

通过设置 `response_format={'type': 'json_object'}`，强制模型输出合法 JSON。

**⚠️ 必须在 prompt 中指示模型生成 JSON**，否则模型可能生成空白直到 token 耗尽。若 `finish_reason="length"`，表示生成超过 max_tokens 或超过最大上下文长度，消息内容可能被截断。

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
    model="deepseek-v4-pro",
    messages=messages,
    response_format={'type': 'json_object'}
)

print(json.loads(response.choices[0].message.content))
```

---

### 4.6 Tool Calls

Tool Calls 让模型能够调用外部工具来增强自身能力。

#### 非思考模式样例

```python
from openai import OpenAI

def send_messages(messages):
    response = client.chat.completions.create(
        model="deepseek-v4-pro",
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

#### 思考模式下的工具调用

从 DeepSeek-V3.2 开始，API 支持思考模式下的工具调用能力。详见 [思考模式 - 工具调用](#41-思考模式)。

#### strict 模式（Beta）

strict 模式下，模型输出 Function 调用时严格遵循 JSON Schema 格式。需设置 `base_url="https://api.deepseek.com/beta"`，且所有 function 设置 `strict: true`。

**使用条件：**
- `base_url="https://api.deepseek.com/beta"`
- 传入的 tools 列表中，所有 function 均需设置 `strict: true`
- 服务端会对 JSON Schema 进行校验，不符合规范将返回错误

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

| 类型 | 支持的约束 | 不支持的参数 |
|------|-----------|-------------|
| object | properties, required, additionalProperties=false（必须设置） | — |
| string | pattern（正则）, format（email/hostname/ipv4/ipv6/uuid） | minLength, maxLength |
| number/integer | const, default, minimum, maximum, exclusiveMinimum, exclusiveMaximum, multipleOf | — |
| array | items | minItems, maxItems |
| enum | 限定输出值范围 | — |
| anyOf | 多 schema 匹配 | — |
| $ref / $def | 模块化引用，支持递归结构 | — |

---

### 4.7 上下文硬盘缓存

DeepSeek API 上下文硬盘缓存技术**对所有用户默认开启**，用户无需修改代码。

**核心原理：** 后续请求与之前请求存在前缀重复时，重复部分从缓存拉取，计入"缓存命中"。

**关键规则：**
- 只有**重复的前缀部分**才能触发缓存命中
- 缓存系统以 64 tokens 为存储单元，不足 64 tokens 不缓存
- "尽力而为"，不保证 100% 命中
- 缓存构建耗时秒级，不再使用后自动清空（几小时到几天）

**查看缓存命中情况（usage 字段）：**
- `prompt_cache_hit_tokens`：缓存命中的 tokens 数（deepseek-v4-flash: 0.2 元 / 百万，deepseek-v4-pro: 1 元 / 百万）
- `prompt_cache_miss_tokens`：缓存未命中的 tokens 数（deepseek-v4-flash: 1 元 / 百万，deepseek-v4-pro: 12 元 / 百万）

**典型场景：**

1. **长文本问答** — 相同 system + 文档前缀 → 命中
2. **多轮对话** — 历史消息前缀一致 → 命中
3. **Few-shot 学习** — 相同示例上下文 → 命中，费用显著降低

> 硬盘缓存只匹配输入前缀，输出仍受 temperature 等参数影响，效果与不使用缓存相同。

---

### 4.8 Anthropic API 兼容

DeepSeek API 支持 Anthropic API 格式，`base_url` 为 `https://api.deepseek.com/anthropic`。

#### 通过 Anthropic SDK 调用

```python
import anthropic

client = anthropic.Anthropic()

message = client.messages.create(
    model="deepseek-v4-pro",
    max_tokens=1000,
    system="You are a helpful assistant.",
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Hi, how are you?"}
            ]
        }
    ]
)
print(message.content)
```

#### Anthropic API 字段兼容性

**Simple Fields：**

| 字段 | 支持状态 |
|------|---------|
| model | 使用 DeepSeek 模型替代 |
| max_tokens | ✅ 完全支持 |
| stop_sequences | ✅ 完全支持 |
| stream | ✅ 完全支持 |
| system | ✅ 完全支持 |
| temperature | ✅ 完全支持（0.0 ~ 2.0） |
| thinking | ✅ 支持（budget_tokens 忽略） |
| top_p | ✅ 完全支持 |
| top_k | 忽略 |
| container / mcp_servers / metadata / service_tier | 忽略 |

**Tool Fields：**

| 字段 | 支持状态 |
|------|---------|
| tools[].name | ✅ 完全支持 |
| tools[].input_schema | ✅ 完全支持 |
| tools[].description | ✅ 完全支持 |
| tools[].cache_control | 忽略 |
| tool_choice: none | ✅ 完全支持 |
| tool_choice: auto/any/tool | ✅ 支持（disable_parallel_tool_use 忽略） |

**Message content 类型：**

| 类型 | 支持状态 |
|------|---------|
| string | ✅ 完全支持 |
| text | ✅ 完全支持 |
| thinking | ✅ 支持 |
| tool_use (id, input, name) | ✅ 完全支持 |
| tool_result (tool_use_id, content) | ✅ 完全支持 |
| image / document / search_result / redacted_thinking | ❌ 不支持 |
| server_tool_use / mcp_tool_use / mcp_tool_result 等 | ❌ 不支持 |

---

## 5. 参数与限制

### 5.1 Temperature 设置

`temperature` 参数默认为 1.0。建议按场景设置：

| 场景 | 推荐温度 |
|------|---------|
| 代码生成 / 数学解题 | 0.0 |
| 数据抽取 / 分析 | 1.0 |
| 通用对话 | 1.3 |
| 翻译 | 1.3 |
| 创意类写作 / 诗歌创作 | 1.5 |

> 思考模式下 temperature、top_p、presence_penalty、frequency_penalty 设置不生效。

---

### 5.2 限速

DeepSeek API 会根据负载情况，**动态限制用户并发量**。当达到并发上限时，会立即收到 HTTP 429 返回。

请求发出后，可能需等待一段时间才能获取响应。等待期间：
- **非流式请求**：持续返回空行
- **流式请求**：持续返回 SSE keep-alive 注释（`: keep-alive`）

这些内容不影响 OpenAI SDK 对 JSON body 的解析。如果自行解析 HTTP 响应，需注意处理空行或注释。

**10 分钟后**若请求仍未开始推理，服务器将关闭连接。

---

### 5.3 错误码

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
