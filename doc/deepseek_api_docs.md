# DeepSeek API 文档

> 本文档整合了DeepSeek API的官方文档，包含快速入门、API参考和高级功能指南。
> 
> 来源：https://api-docs.deepseek.com/zh-cn/


---

## 首次调用

- 

- 快速开始
- 首次调用 API
本页总览
# 首次调用 API

DeepSeek API 使用与 OpenAI 兼容的 API 格式，通过修改配置，您可以使用 OpenAI SDK 来访问 DeepSeek API，或使用与 OpenAI API 兼容的软件。

| PARAM | VALUE | |
| base_url *        `https://api.deepseek.com` | |
| api_key | apply for an [API key](https://platform.deepseek.com/api_keys) | |

* 出于与 OpenAI 兼容考虑，您也可以将 `base_url` 设置为 `https://api.deepseek.com/v1` 来使用，但注意，此处 `v1` 与模型版本无关。

* **`deepseek-chat` 和 `deepseek-reasoner` 对应模型版本不变，为 DeepSeek-V3.2 (128K 上下文长度)，与 APP/WEB 版不同。**`deepseek-chat` 对应 DeepSeek-V3.2 的**非思考模式**，`deepseek-reasoner` 对应 DeepSeek-V3.2 的**思考模式**。

## 调用对话 API[​](#调用对话-api)

在创建 API key 之后，你可以使用以下样例脚本的来访问 DeepSeek API。样例为非流式输出，您可以将 stream 设置为 true 来使用流式输出。

- curl
- python
- nodejs

```bash
curl https://api.deepseek.com/chat/completions \
 -H "Content-Type: application/json" \
 -H "Authorization: Bearer ${DEEPSEEK_API_KEY}" \
 -d '{
 "model": "deepseek-chat",
 "messages": [
 {"role": "system", "content": "You are a helpful assistant."},
 {"role": "user", "content": "Hello!"}
 ],
 "stream": false
 }'

```

```
# Please install OpenAI SDK first: `pip3 install openai`
import os
from openai import OpenAI

client = OpenAI(
 api_key=os.environ.get('DEEPSEEK_API_KEY'),
 base_url="https://api.deepseek.com")

response = client.chat.completions.create(
 model="deepseek-chat",
 messages=[
 {"role": "system", "content": "You are a helpful assistant"},
 {"role": "user", "content": "Hello"},
 ],
 stream=False
)

print(response.choices[0].message.content)

```

```
// Please install OpenAI SDK first: `npm install openai`

import OpenAI from "openai";

const openai = new OpenAI({
 baseURL: 'https://api.deepseek.com',
 apiKey: process.env.DEEPSEEK_API_KEY,
});

async function main() {
 const completion = await openai.chat.completions.create({
 messages: [{ role: "system", content: "You are a helpful assistant." }],
 model: "deepseek-chat",
 });

 console.log(completion.choices[0].message.content);
}

main();

```

---

## 对话补全 API

- 

- API 文档
- 对话（Chat）
- 对话补全

# 对话补全

POST 
## https://api.deepseek.com/chat/completions

根据输入的上下文，来让模型补全对话内容。

## Request[​](#request)

- application/json

### 
Body

**
required
**
**
messages
**
object[]

required

**Possible values:** `>= 1`

对话的消息列表。

- 
Array [

oneOf

- System message
- User message
- Assistant message
- Tool message
**content** stringrequired
system 消息的内容。
**role** stringrequired
**Possible values:** [`system`]

该消息的发起角色，其值为 `system`。
**name** string
可以选填的参与者的名称，为模型提供信息以区分相同角色的参与者。
**content** Text content (string)required
user 消息的内容。
**role** stringrequired
**Possible values:** [`user`]

该消息的发起角色，其值为 `user`。
**name** string
可以选填的参与者的名称，为模型提供信息以区分相同角色的参与者。
**content** stringnullablerequired
assistant 消息的内容。
**role** stringrequired
**Possible values:** [`assistant`]

该消息的发起角色，其值为 `assistant`。
**name** string
可以选填的参与者的名称，为模型提供信息以区分相同角色的参与者。
**prefix** bool
(Beta) 设置此参数为 true，来强制模型在其回答中以此 `assistant` 消息中提供的前缀内容开始。

您必须设置 `base_url="https://api.deepseek.com/beta"` 来使用此功能。
**reasoning_content** stringnullable
(Beta) 用于 `deepseek-reasoner` 模型在[对话前缀续写](/zh-cn/guides/chat_prefix_completion)功能下，作为最后一条 assistant 思维链内容的输入。使用此功能时，`prefix` 参数必须设置为 `true`。
**role** stringrequired
**Possible values:** [`tool`]

该消息的发起角色，其值为 `tool`。
**content** Text content (string)required
tool 消息的内容。
**tool_call_id** stringrequired
此消息所响应的 tool call 的 ID。

- 
]
**model** stringrequired
**Possible values:** [`deepseek-chat`, `deepseek-reasoner`]

使用的模型的 ID。您可以使用 deepseek-chat。
**
thinking
**
object

nullable

控制思考模式与非思考模式的转换
**type** string
**Possible values:** [`enabled`, `disabled`]

如果设为 `enabled`，则使用思考模式。如果设为 `disabled`，则使用非思考模式
**frequency_penalty** numbernullable
**Possible values:** `>= -2` and `<= 2`

**Default value:** `0`

介于 -2.0 和 2.0 之间的数字。如果该值为正，那么新 token 会根据其在已有文本中的出现频率受到相应的惩罚，降低模型重复相同内容的可能性。
**max_tokens** integernullable
限制一次请求中模型生成 completion 的最大 token 数。输入 token 和输出 token 的总长度受模型的上下文长度的限制。取值范围与默认值详见[文档](/zh-cn/quick_start/pricing)。
**presence_penalty** numbernullable
**Possible values:** `>= -2` and `<= 2`

**Default value:** `0`

介于 -2.0 和 2.0 之间的数字。如果该值为正，那么新 token 会根据其是否已在已有文本中出现受到相应的惩罚，从而增加模型谈论新主题的可能性。
**
response_format
**
object

nullable

一个 object，指定模型必须输出的格式。

设置为 { "type": "json_object" } 以启用 JSON 模式，该模式保证模型生成的消息是有效的 JSON。

**注意:** 使用 JSON 模式时，你还必须通过系统或用户消息指示模型生成 JSON。否则，模型可能会生成不断的空白字符，直到生成达到令牌限制，从而导致请求长时间运行并显得“卡住”。此外，如果 finish_reason="length"，这表示生成超过了 max_tokens 或对话超过了最大上下文长度，消息内容可能会被部分截断。
**type** string
**Possible values:** [`text`, `json_object`]

**Default value:** `text`

Must be one of `text` or `json_object`.
**
stop
**
object
**
nullable
**
一个 string 或最多包含 16 个 string 的 list，在遇到这些词时，API 将停止生成更多的 token。

oneOf

- MOD1
- MOD2

string

- 
Array [

string

- 
]
**stream** booleannullable
如果设置为 True，将会以 SSE（server-sent events）的形式以流式发送消息增量。消息流以 `data: [DONE]` 结尾。
**
stream_options
**
object

nullable

流式输出相关选项。只有在 `stream` 参数为 `true` 时，才可设置此参数。
**include_usage** boolean
如果设置为 true，在流式消息最后的 `data: [DONE]` 之前将会传输一个额外的块。此块上的 usage 字段显示整个请求的 token 使用统计信息，而 choices 字段将始终是一个空数组。所有其他块也将包含一个 usage 字段，但其值为 null。
**temperature** numbernullable
**Possible values:** `<= 2`

**Default value:** `1`

采样温度，介于 0 和 2 之间。更高的值，如 0.8，会使输出更随机，而更低的值，如 0.2，会使其更加集中和确定。 我们通常建议可以更改这个值或者更改 `top_p`，但不建议同时对两者进行修改。
**top_p** numbernullable
**Possible values:** `<= 1`

**Default value:** `1`

作为调节采样温度的替代方案，模型会考虑前 `top_p` 概率的 token 的结果。所以 0.1 就意味着只有包括在最高 10% 概率中的 token 会被考虑。 我们通常建议修改这个值或者更改 `temperature`，但不建议同时对两者进行修改。
**
tools
**
object[]

nullable

模型可能会调用的 tool 的列表。目前，仅支持 function 作为工具。使用此参数来提供以 JSON 作为输入参数的 function 列表。最多支持 128 个 function。

- 
Array [
**type** stringrequired
**Possible values:** [`function`]

tool 的类型。目前仅支持 function。
**
function
**
object

required
**description** string
function 的功能描述，供模型理解何时以及如何调用该 function。
**name** stringrequired
要调用的 function 名称。必须由 a-z、A-Z、0-9 字符组成，或包含下划线和连字符，最大长度为 64 个字符。
**
parameters
**
object

function 的输入参数，以 JSON Schema 对象描述。请参阅[Tool Calls 指南](/zh-cn/guides/tool_calls)获取示例，并参阅[JSON Schema 参考](https://json-schema.org/understanding-json-schema/)了解有关格式的文档。省略 `parameters` 会定义一个参数列表为空的 function。
**property name*** any
function 的输入参数，以 JSON Schema 对象描述。请参阅[Tool Calls 指南](/zh-cn/guides/tool_calls)获取示例，并参阅[JSON Schema 参考](https://json-schema.org/understanding-json-schema/)了解有关格式的文档。省略 `parameters` 会定义一个参数列表为空的 function。
**strict** boolean
**Default value:** `false`

如果设置为 true，API 将在函数调用中使用 strict 模式，以确保输出始终符合函数的 JSON schema 定义。该功能为 Beta 功能，详细使用方式请参阅[Tool Calls 指南](/zh-cn/guides/tool_calls)

- 
]
**
tool_choice
**
object
**
nullable
**
控制模型调用 tool 的行为。

`none` 意味着模型不会调用任何 tool，而是生成一条消息。

`auto` 意味着模型可以选择生成一条消息或调用一个或多个 tool。

`required` 意味着模型必须调用一个或多个 tool。

通过 `{"type": "function", "function": {"name": "my_function"}}` 指定特定 tool，会强制模型调用该 tool。

当没有 tool 时，默认值为 `none`。如果有 tool 存在，默认值为 `auto`。

oneOf

- ChatCompletionToolChoice
- ChatCompletionNamedToolChoice

string

**Possible values:** [`none`, `auto`, `required`]
**type** stringrequired
**Possible values:** [`function`]

tool 的类型。目前，仅支持 `function`。
**
function
**
object

required
**name** stringrequired
要调用的函数名称。
**logprobs** booleannullable
是否返回所输出 token 的对数概率。如果为 true，则在 `message` 的 `content` 中返回每个输出 token 的对数概率。
**top_logprobs** integernullable
**Possible values:** `<= 20`

一个介于 0 到 20 之间的整数 N，指定每个输出位置返回输出概率 top N 的 token，且返回这些 token 的对数概率。指定此参数时，logprobs 必须为 true。

## Responses[​](#responses)

- 200 (No streaming)
- 200 (Streaming)

OK, 返回一个 `chat completion` 对象。

- application/json

- Schema
- Example (from schema)
- Example
**
Schema
**
**id** stringrequired
该对话的唯一标识符。
**
choices
**
object[]

required

模型生成的 completion 的选择列表。

- 
Array [
**finish_reason** stringrequired
**Possible values:** [`stop`, `length`, `content_filter`, `tool_calls`, `insufficient_system_resource`]

模型停止生成 token 的原因。

`stop`：模型自然停止生成，或遇到 `stop` 序列中列出的字符串。

`length` ：输出长度达到了模型上下文长度限制，或达到了 `max_tokens` 的限制。

`content_filter`：输出内容因触发过滤策略而被过滤。

`insufficient_system_resource`：系统推理资源不足，生成被打断。
**index** integerrequired
该 completion 在模型生成的 completion 的选择列表中的索引。
**
message
**
object

required

模型生成的 completion 消息。
**content** stringnullablerequired
该 completion 的内容。
**reasoning_content** stringnullable
仅适用于 deepseek-reasoner 模型。内容为 assistant 消息中在最终答案之前的推理内容。
**
tool_calls
**
object[]

模型生成的 tool 调用，例如 function 调用。

- 
Array [
**id** stringrequired
tool 调用的 ID。
**type** stringrequired
**Possible values:** [`function`]

tool 的类型。目前仅支持 `function`。
**
function
**
object

required

模型调用的 function。
**name** stringrequired
模型调用的 function 名。
**arguments** stringrequired
要调用的 function 的参数，由模型生成，格式为 JSON。请注意，模型并不总是生成有效的 JSON，并且可能会臆造出你函数模式中未定义的参数。在调用函数之前，请在代码中验证这些参数。

- 
]
**role** stringrequired
**Possible values:** [`assistant`]

生成这条消息的角色。
**
logprobs
**
object

nullable

required

该 choice 的对数概率信息。
**
content
**
object[]

nullable

required

一个包含输出 token 对数概率信息的列表。

- 
Array [
**token** stringrequired
输出的 token。
**logprob** numberrequired
该 token 的对数概率。`-9999.0` 代表该 token 的输出概率极小，不在 top 20 最可能输出的 token 中。
**bytes** integer[]nullablerequired
一个包含该 token UTF-8 字节表示的整数列表。一般在一个 UTF-8 字符被拆分成多个 token 来表示时有用。如果 token 没有对应的字节表示，则该值为 `null`。
**
top_logprobs
**
object[]

required

一个包含在该输出位置上，输出概率 top N 的 token 的列表，以及它们的对数概率。在罕见情况下，返回的 token 数量可能少于请求参数中指定的 `top_logprobs` 值。

- 
Array [
**token** stringrequired
输出的 token。
**logprob** numberrequired
该 token 的对数概率。`-9999.0` 代表该 token 的输出概率极小，不在 top 20 最可能输出的 token 中。
**bytes** integer[]nullablerequired
一个包含该 token UTF-8 字节表示的整数列表。一般在一个 UTF-8 字符被拆分成多个 token 来表示时有用。如果 token 没有对应的字节表示，则该值为 `null`。

- 
]

- 
]
**
reasoning_content
**
object[]

nullable

一个包含输出 token 对数概率信息的列表。

- 
Array [
**token** stringrequired
输出的 token。
**logprob** numberrequired
该 token 的对数概率。`-9999.0` 代表该 token 的输出概率极小，不在 top 20 最可能输出的 token 中。
**bytes** integer[]nullablerequired
一个包含该 token UTF-8 字节表示的整数列表。一般在一个 UTF-8 字符被拆分成多个 token 来表示时有用。如果 token 没有对应的字节表示，则该值为 `null`。
**
top_logprobs
**
object[]

required

一个包含在该输出位置上，输出概率 top N 的 token 的列表，以及它们的对数概率。在罕见情况下，返回的 token 数量可能少于请求参数中指定的 `top_logprobs` 值。

- 
Array [
**token** stringrequired
输出的 token。
**logprob** numberrequired
该 token 的对数概率。`-9999.0` 代表该 token 的输出概率极小，不在 top 20 最可能输出的 token 中。
**bytes** integer[]nullablerequired
一个包含该 token UTF-8 字节表示的整数列表。一般在一个 UTF-8 字符被拆分成多个 token 来表示时有用。如果 token 没有对应的字节表示，则该值为 `null`。

- 
]

- 
]

- 
]
**created** integerrequired
创建聊天完成时的 Unix 时间戳（以秒为单位）。
**model** stringrequired
生成该 completion 的模型名。
**system_fingerprint** stringrequired
This fingerprint represents the backend configuration that the model runs with.
**object** stringrequired
**Possible values:** [`chat.completion`]

对象的类型, 其值为 `chat.completion`。
**
usage
**
object

该对话补全请求的用量信息。
**completion_tokens** integerrequired
模型 completion 产生的 token 数。
**prompt_tokens** integerrequired
用户 prompt 所包含的 token 数。该值等于 `prompt_cache_hit_tokens + prompt_cache_miss_tokens`
**prompt_cache_hit_tokens** integerrequired
用户 prompt 中，命中上下文缓存的 token 数。
**prompt_cache_miss_tokens** integerrequired
用户 prompt 中，未命中上下文缓存的 token 数。
**total_tokens** integerrequired
该请求中，所有 token 的数量（prompt + completion）。
**
completion_tokens_details
**
object

completion tokens 的详细信息。
**reasoning_tokens** integer
推理模型所产生的思维链 token 数量

```
{
 "id": "string",
 "choices": [
 {
 "finish_reason": "stop",
 "index": 0,
 "message": {
 "content": "string",
 "reasoning_content": "string",
 "tool_calls": [
 {
 "id": "string",
 "type": "function",
 "function": {
 "name": "string",
 "arguments": "string"
 }
 }
 ],
 "role": "assistant"
 },
 "logprobs": {
 "content": [
 {
 "token": "string",
 "logprob": 0,
 "bytes": [
 0
 ],
 "top_logprobs": [
 {
 "token": "string",
 "logprob": 0,
 "bytes": [
 0
 ]
 }
 ]
 }
 ],
 "reasoning_content": [
 {
 "token": "string",
 "logprob": 0,
 "bytes": [
 0
 ],
 "top_logprobs": [
 {
 "token": "string",
 "logprob": 0,
 "bytes": [
 0
 ]
 }
 ]
 }
 ]
 }
 }
 ],
 "created": 0,
 "model": "string",
 "system_fingerprint": "string",
 "object": "chat.completion",
 "usage": {
 "completion_tokens": 0,
 "prompt_tokens": 0,
 "prompt_cache_hit_tokens": 0,
 "prompt_cache_miss_tokens": 0,
 "total_tokens": 0,
 "completion_tokens_details": {
 "reasoning_tokens": 0
 }
 }
}

```

```
{
 "id": "930c60df-bf64-41c9-a88e-3ec75f81e00e",
 "choices": [
 {
 "finish_reason": "stop",
 "index": 0,
 "message": {
 "content": "Hello! How can I help you today?",
 "role": "assistant"
 }
 }
 ],
 "created": 1705651092,
 "model": "deepseek-chat",
 "object": "chat.completion",
 "usage": {
 "completion_tokens": 10,
 "prompt_tokens": 16,
 "total_tokens": 26
 }
}

```
OK, 返回包含一系列 `chat completion chunk` 对象的流式输出。

- text/event-stream

- Schema
- Example
**
Schema
**

- 
Array [
**id** stringrequired
该对话的唯一标识符。
**
choices
**
object[]

required

模型生成的 completion 的选择列表。

- 
Array [
**
delta
**
object

required

流式返回的一个 completion 增量。
**content** stringnullable
completion 增量的内容。
**reasoning_content** stringnullable
仅适用于 deepseek-reasoner 模型。内容为 assistant 消息中在最终答案之前的推理内容。
**role** string
**Possible values:** [`assistant`]

产生这条消息的角色。
**
logprobs
**
object

nullable

该 choice 的对数概率信息。
**
content
**
object[]

nullable

required

一个包含输出 token 对数概率信息的列表。

- 
Array [
**token** stringrequired
输出的 token。
**logprob** numberrequired
该 token 的对数概率。`-9999.0` 代表该 token 的输出概率极小，不在 top 20 最可能输出的 token 中。
**bytes** integer[]nullablerequired
一个包含该 token UTF-8 字节表示的整数列表。一般在一个 UTF-8 字符被拆分成多个 token 来表示时有用。如果 token 没有对应的字节表示，则该值为 `null`。
**
top_logprobs
**
object[]

required

一个包含在该输出位置上，输出概率 top N 的 token 的列表，以及它们的对数概率。在罕见情况下，返回的 token 数量可能少于请求参数中指定的 `top_logprobs` 值。

- 
Array [
**token** stringrequired
输出的 token。
**logprob** numberrequired
该 token 的对数概率。`-9999.0` 代表该 token 的输出概率极小，不在 top 20 最可能输出的 token 中。
**bytes** integer[]nullablerequired
一个包含该 token UTF-8 字节表示的整数列表。一般在一个 UTF-8 字符被拆分成多个 token 来表示时有用。如果 token 没有对应的字节表示，则该值为 `null`。

- 
]

- 
]
**
reasoning_content
**
object[]

nullable

一个包含输出 token 对数概率信息的列表。

- 
Array [
**token** stringrequired
输出的 token。
**logprob** numberrequired
该 token 的对数概率。`-9999.0` 代表该 token 的输出概率极小，不在 top 20 最可能输出的 token 中。
**bytes** integer[]nullablerequired
一个包含该 token UTF-8 字节表示的整数列表。一般在一个 UTF-8 字符被拆分成多个 token 来表示时有用。如果 token 没有对应的字节表示，则该值为 `null`。
**
top_logprobs
**
object[]

required

一个包含在该输出位置上，输出概率 top N 的 token 的列表，以及它们的对数概率。在罕见情况下，返回的 token 数量可能少于请求参数中指定的 `top_logprobs` 值。

- 
Array [
**token** stringrequired
输出的 token。
**logprob** numberrequired
该 token 的对数概率。`-9999.0` 代表该 token 的输出概率极小，不在 top 20 最可能输出的 token 中。
**bytes** integer[]nullablerequired
一个包含该 token UTF-8 字节表示的整数列表。一般在一个 UTF-8 字符被拆分成多个 token 来表示时有用。如果 token 没有对应的字节表示，则该值为 `null`。

- 
]

- 
]
**finish_reason** stringnullablerequired
**Possible values:** [`stop`, `length`, `content_filter`, `tool_calls`, `insufficient_system_resource`]

模型停止生成 token 的原因。

`stop`：模型自然停止生成，或遇到 `stop` 序列中列出的字符串。

`length` ：输出长度达到了模型上下文长度限制，或达到了 `max_tokens` 的限制。

`content_filter`：输出内容因触发过滤策略而被过滤。

`insufficient_system_resource`: 由于后端推理资源受限，请求被打断。
**index** integerrequired
该 completion 在模型生成的 completion 的选择列表中的索引。

- 
]
**created** integerrequired
创建聊天完成时的 Unix 时间戳（以秒为单位）。流式响应的每个 chunk 的时间戳相同。
**model** stringrequired
生成该 completion 的模型名。
**system_fingerprint** stringrequired
This fingerprint represents the backend configuration that the model runs with.
**object** stringrequired
**Possible values:** [`chat.completion.chunk`]

对象的类型, 其值为 `chat.completion.chunk`。

- 
]

```
data: {"id": "1f633d8bfc032625086f14113c411638", "choices": [{"index": 0, "delta": {"content": "", "role": "assistant"}, "finish_reason": null, "logprobs": null}], "created": 1718345013, "model": "deepseek-chat", "system_fingerprint": "fp_a49d71b8a1", "object": "chat.completion.chunk", "usage": null}

data: {"choices": [{"delta": {"content": "Hello", "role": "assistant"}, "finish_reason": null, "index": 0, "logprobs": null}], "created": 1718345013, "id": "1f633d8bfc032625086f14113c411638", "model": "deepseek-chat", "object": "chat.completion.chunk", "system_fingerprint": "fp_a49d71b8a1"}

data: {"choices": [{"delta": {"content": "!", "role": "assistant"}, "finish_reason": null, "index": 0, "logprobs": null}], "created": 1718345013, "id": "1f633d8bfc032625086f14113c411638", "model": "deepseek-chat", "object": "chat.completion.chunk", "system_fingerprint": "fp_a49d71b8a1"}

data: {"choices": [{"delta": {"content": " How", "role": "assistant"}, "finish_reason": null, "index": 0, "logprobs": null}], "created": 1718345013, "id": "1f633d8bfc032625086f14113c411638", "model": "deepseek-chat", "object": "chat.completion.chunk", "system_fingerprint": "fp_a49d71b8a1"}

data: {"choices": [{"delta": {"content": " can", "role": "assistant"}, "finish_reason": null, "index": 0, "logprobs": null}], "created": 1718345013, "id": "1f633d8bfc032625086f14113c411638", "model": "deepseek-chat", "object": "chat.completion.chunk", "system_fingerprint": "fp_a49d71b8a1"}

data: {"choices": [{"delta": {"content": " I", "role": "assistant"}, "finish_reason": null, "index": 0, "logprobs": null}], "created": 1718345013, "id": "1f633d8bfc032625086f14113c411638", "model": "deepseek-chat", "object": "chat.completion.chunk", "system_fingerprint": "fp_a49d71b8a1"}

data: {"choices": [{"delta": {"content": " assist", "role": "assistant"}, "finish_reason": null, "index": 0, "logprobs": null}], "created": 1718345013, "id": "1f633d8bfc032625086f14113c411638", "model": "deepseek-chat", "object": "chat.completion.chunk", "system_fingerprint": "fp_a49d71b8a1"}

data: {"choices": [{"delta": {"content": " you", "role": "assistant"}, "finish_reason": null, "index": 0, "logprobs": null}], "created": 1718345013, "id": "1f633d8bfc032625086f14113c411638", "model": "deepseek-chat", "object": "chat.completion.chunk", "system_fingerprint": "fp_a49d71b8a1"}

data: {"choices": [{"delta": {"content": " today", "role": "assistant"}, "finish_reason": null, "index": 0, "logprobs": null}], "created": 1718345013, "id": "1f633d8bfc032625086f14113c411638", "model": "deepseek-chat", "object": "chat.completion.chunk", "system_fingerprint": "fp_a49d71b8a1"}

data: {"choices": [{"delta": {"content": "?", "role": "assistant"}, "finish_reason": null, "index": 0, "logprobs": null}], "created": 1718345013, "id": "1f633d8bfc032625086f14113c411638", "model": "deepseek-chat", "object": "chat.completion.chunk", "system_fingerprint": "fp_a49d71b8a1"}

data: {"choices": [{"delta": {"content": "", "role": null}, "finish_reason": "stop", "index": 0, "logprobs": null}], "created": 1718345013, "id": "1f633d8bfc032625086f14113c411638", "model": "deepseek-chat", "object": "chat.completion.chunk", "system_fingerprint": "fp_a49d71b8a1", "usage": {"completion_tokens": 9, "prompt_tokens": 17, "total_tokens": 26}}

data: [DONE]

```

- curl
- python
- go
- nodejs
- ruby
- csharp
- php
- java
- powershell

- CURL

```bash
curl -L -X POST 'https://api.deepseek.com/chat/completions' \
-H 'Content-Type: application/json' \
-H 'Accept: application/json' \
-H 'Authorization: Bearer <TOKEN>' \
--data-raw '{
 "messages": [
 {
 "content": "You are a helpful assistant",
 "role": "system"
 },
 {
 "content": "Hi",
 "role": "user"
 }
 ],
 "model": "deepseek-chat",
 "thinking": {
 "type": "disabled"
 },
 "frequency_penalty": 0,
 "max_tokens": 4096,
 "presence_penalty": 0,
 "response_format": {
 "type": "text"
 },
 "stop": null,
 "stream": false,
 "stream_options": null,
 "temperature": 1,
 "top_p": 1,
 "tools": null,
 "tool_choice": "none",
 "logprobs": false,
 "top_logprobs": null
}'

```
Request Collapse allBase URLEdithttps://api.deepseek.comAuthBearer TokenBody required{
 "messages": [
 {
 "content": "You are a helpful assistant",
 "role": "system"
 },
 {
 "content": "Hi",
 "role": "user"
 }
 ],
 "model": "deepseek-chat",
 "thinking": {
 "type": "disabled"
 },
 "frequency_penalty": 0,
 "max_tokens": 4096,
 "presence_penalty": 0,
 "response_format": {
 "type": "text"
 },
 "stop": null,
 "stream": false,
 "stream_options": null,
 "temperature": 1,
 "top_p": 1,
 "tools": null,
 "tool_choice": "none",
 "logprobs": false,
 "top_logprobs": null
}
Send API RequestResponseClearClick the `Send API Request` button above and see the response here!

---

## FIM补全 API

- 

- API 文档
- 补全（Completions）
- FIM 补全（Beta）

# FIM 补全（Beta）

POST 
## https://api.deepseek.com/beta/completions

FIM（Fill-In-the-Middle）补全 API。

用户需要设置 `base_url="https://api.deepseek.com/beta"` 来使用此功能。

## Request[​](#request)

- application/json

### 
Body

**
required
**
**model** stringrequired
**Possible values:** [`deepseek-chat`]

模型的 ID
**prompt** stringrequired
**Default value:** `Once upon a time, `

用于生成完成内容的提示
**echo** booleannullable
在输出中，把 prompt 的内容也输出出来
**frequency_penalty** numbernullable
**Possible values:** `>= -2` and `<= 2`

**Default value:** `0`

介于 -2.0 和 2.0 之间的数字。如果该值为正，那么新 token 会根据其在已有文本中的出现频率受到相应的惩罚，降低模型重复相同内容的可能性。
**logprobs** integernullable
**Possible values:** `<= 20`

制定输出中包含 logprobs 最可能输出 token 的对数概率，包含采样的 token。例如，如果 logprobs 是 20，API 将返回一个包含 20 个最可能的 token 的列表。API 将始终返回采样 token 的对数概率，因此响应中可能会有最多 logprobs+1 个元素。

logprobs 的最大值是 20。
**max_tokens** integernullable
最大生成 token 数量。
**presence_penalty** numbernullable
**Possible values:** `>= -2` and `<= 2`

**Default value:** `0`

介于 -2.0 和 2.0 之间的数字。如果该值为正，那么新 token 会根据其是否已在已有文本中出现受到相应的惩罚，从而增加模型谈论新主题的可能性。
**
stop
**
object
**
nullable
**
一个 string 或最多包含 16 个 string 的 list，在遇到这些词时，API 将停止生成更多的 token。

oneOf

- MOD1
- MOD2

string

- 
Array [

string

- 
]
**stream** booleannullable
如果设置为 True，将会以 SSE（server-sent events）的形式以流式发送消息增量。消息流以 `data: [DONE]` 结尾。
**
stream_options
**
object

nullable

流式输出相关选项。只有在 `stream` 参数为 `true` 时，才可设置此参数。
**include_usage** boolean
如果设置为 true，在流式消息最后的 `data: [DONE]` 之前将会传输一个额外的块。此块上的 usage 字段显示整个请求的 token 使用统计信息，而 choices 字段将始终是一个空数组。所有其他块也将包含一个 usage 字段，但其值为 null。
**suffix** stringnullable
制定被补全内容的后缀。
**temperature** numbernullable
**Possible values:** `<= 2`

**Default value:** `1`

采样温度，介于 0 和 2 之间。更高的值，如 0.8，会使输出更随机，而更低的值，如 0.2，会使其更加集中和确定。 我们通常建议可以更改这个值或者更改 `top_p`，但不建议同时对两者进行修改。
**top_p** numbernullable
**Possible values:** `<= 1`

**Default value:** `1`

作为调节采样温度的替代方案，模型会考虑前 `top_p` 概率的 token 的结果。所以 0.1 就意味着只有包括在最高 10% 概率中的 token 会被考虑。 我们通常建议修改这个值或者更改 `temperature`，但不建议同时对两者进行修改。

## Responses[​](#responses)

- 200

OK

- application/json

- Schema
- Example (from schema)
**
Schema
**
**id** stringrequired
补全响应的 ID。
**
choices
**
object[]

required

模型生成的补全内容的选择列表。

- 
Array [
**finish_reason** stringrequired
**Possible values:** [`stop`, `length`, `content_filter`, `insufficient_system_resource`]

模型停止生成 token 的原因。

`stop`：模型自然停止生成，或遇到 `stop` 序列中列出的字符串。

`length` ：输出长度达到了模型上下文长度限制，或达到了 `max_tokens` 的限制。

`content_filter`：输出内容因触发过滤策略而被过滤。

`insufficient_system_resource`: 由于后端推理资源受限，请求被打断。
**index** integerrequired**
logprobs
**
object

nullable

required
**text_offset** integer[]**token_logprobs** number[]**tokens** string[]**top_logprobs** object[]**text** stringrequired
- 
]
**created** integerrequired
标志补全请求开始时间的 Unix 时间戳（以秒为单位）。
**model** stringrequired
补全请求所用的模型。
**system_fingerprint** string
模型运行时的后端配置的指纹。
**object** stringrequired
**Possible values:** [`text_completion`]

object 的类型，一定为"text_completion"
**
usage
**
object

该对话补全请求的用量信息。
**completion_tokens** integerrequired
模型 completion 产生的 token 数。
**prompt_tokens** integerrequired
用户 prompt 所包含的 token 数。该值等于 `prompt_cache_hit_tokens + prompt_cache_miss_tokens`
**prompt_cache_hit_tokens** integerrequired
用户 prompt 中，命中上下文缓存的 token 数。
**prompt_cache_miss_tokens** integerrequired
用户 prompt 中，未命中上下文缓存的 token 数。
**total_tokens** integerrequired
该请求中，所有 token 的数量（prompt + completion）。
**
completion_tokens_details
**
object

completion tokens 的详细信息。
**reasoning_tokens** integer
推理模型所产生的思维链 token 数量

```
{
 "id": "string",
 "choices": [
 {
 "finish_reason": "stop",
 "index": 0,
 "logprobs": {
 "text_offset": [
 0
 ],
 "token_logprobs": [
 0
 ],
 "tokens": [
 "string"
 ],
 "top_logprobs": [
 {}
 ]
 },
 "text": "string"
 }
 ],
 "created": 0,
 "model": "string",
 "system_fingerprint": "string",
 "object": "text_completion",
 "usage": {
 "completion_tokens": 0,
 "prompt_tokens": 0,
 "prompt_cache_hit_tokens": 0,
 "prompt_cache_miss_tokens": 0,
 "total_tokens": 0,
 "completion_tokens_details": {
 "reasoning_tokens": 0
 }
 }
}

```

- curl
- python
- go
- nodejs
- ruby
- csharp
- php
- java
- powershell

- CURL

```bash
curl -L -X POST 'https://api.deepseek.com/beta/completions' \
-H 'Content-Type: application/json' \
-H 'Accept: application/json' \
-H 'Authorization: Bearer <TOKEN>' \
--data-raw '{
 "model": "deepseek-chat",
 "prompt": "Once upon a time, ",
 "echo": false,
 "frequency_penalty": 0,
 "logprobs": 0,
 "max_tokens": 1024,
 "presence_penalty": 0,
 "stop": null,
 "stream": false,
 "stream_options": null,
 "suffix": null,
 "temperature": 1,
 "top_p": 1
}'

```
Request Collapse allBase URLEdithttps://api.deepseek.com/betaAuthBearer TokenBody required{
 "model": "deepseek-chat",
 "prompt": "Once upon a time, ",
 "echo": false,
 "frequency_penalty": 0,
 "logprobs": 0,
 "max_tokens": 1024,
 "presence_penalty": 0,
 "stop": null,
 "stream": false,
 "stream_options": null,
 "suffix": null,
 "temperature": 1,
 "top_p": 1
}
Send API RequestResponseClearClick the `Send API Request` button above and see the response here!

---

## 思考模式

- 

- API 指南
- 思考模式
本页总览
# 思考模式

DeepSeek 模型支持思考模式：在输出最终回答之前，模型会先输出一段思维链内容，以提升最终答案的准确性。您可以通过以下任意一种方式，开启思考模式：

- 

设置 `model` 参数：`"model": "deepseek-reasoner"`

- 

设置 `thinking` 参数：`"thinking": {"type": "enabled"}`

如果您使用的是 OpenAI SDK，在设置 `thinking` 参数时，需要将 `thinking` 参数传入 `extra_body` 中：

```
response = client.chat.completions.create(
 model="deepseek-chat",
 # ...
 extra_body={"thinking": {"type": "enabled"}}
)

```

## API 参数[​](#api-参数)

- 
**输入参数**：

`max_tokens`：模型单次回答的最大长度（含思维链输出），默认为 32K，最大为 64K。

- 

**输出字段**：

`reasoning_content`：思维链内容，与 `content` 同级，访问方法见[样例代码](#%E6%A0%B7%E4%BE%8B%E4%BB%A3%E7%A0%81)。

- `content`：最终回答内容。

- `tool_calls`: 模型工具调用。

- 

**支持的功能**：[Json Output](/zh-cn/guides/json_mode)、[Tool Calls](/zh-cn/guides/tool_calls)、[对话补全](/zh-cn/api/create-chat-completion)，[对话前缀续写 (Beta)](/zh-cn/guides/chat_prefix_completion)

- 

**不支持的功能**：FIM 补全 (Beta)

- 

**不支持的参数**：`temperature`、`top_p`、`presence_penalty`、`frequency_penalty`、`logprobs`、`top_logprobs`。请注意，为了兼容已有软件，设置 `temperature`、`top_p`、`presence_penalty`、`frequency_penalty` 参数不会报错，但也不会生效。设置 `logprobs`、`top_logprobs` 会报错。

## 多轮对话拼接[​](#多轮对话拼接)

在每一轮对话过程中，模型会输出思维链内容（`reasoning_content`）和最终回答（`content`）。在下一轮对话中，之前轮输出的思维链内容不会被拼接到上下文中，如下图所示：

### 样例代码[​](#样例代码)

下面的代码以 Python 语言为例，展示了如何访问思维链和最终回答，以及如何在多轮对话中进行上下文拼接。注意代码中在新一轮对话里，只传入了上一轮输出的 `content`，而忽略了 `reasoning_content`。

- 非流式
- 流式

```
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

# Turn 2
messages.append({'role': 'assistant', 'content': content})
messages.append({'role': 'user', 'content': "How many Rs are there in the word 'strawberry'?"})
response = client.chat.completions.create(
 model="deepseek-reasoner",
 messages=messages
)
# ...

```

```
from openai import OpenAI
client = OpenAI(api_key="<DeepSeek API Key>", base_url="https://api.deepseek.com")

# Turn 1
messages = [{"role": "user", "content": "9.11 and 9.8, which is greater?"}]
response = client.chat.completions.create(
 model="deepseek-reasoner",
 messages=messages,
 stream=True
)

reasoning_content = ""
content = ""

for chunk in response:
 if chunk.choices[0].delta.reasoning_content:
 reasoning_content += chunk.choices[0].delta.reasoning_content
 else:
 content += chunk.choices[0].delta.content

# Turn 2
messages.append({"role": "assistant", "content": content})
messages.append({'role': 'user', 'content': "How many Rs are there in the word 'strawberry'?"})
response = client.chat.completions.create(
 model="deepseek-reasoner",
 messages=messages,
 stream=True
)
# ...

```

## 工具调用[​](#工具调用)

我们为 DeepSeek 模型的思考模式增加了工具调用功能。模型在输出最终答案之前，可以进行多轮的思考与工具调用，以提升答案的质量。其调用模式如下图所示：

- 在回答问题 1 过程中（请求 1.1 - 1.3），模型进行了多次思考 + 工具调用后给出答案。在这个过程中，用户需回传思维链内容（reasoning_content）给 API，以让模型继续思考。

- 在下一个用户问题开始时（请求 2.1），需删除之前的 `reasoning_content`，并保留其它内容发送给 API。如果保留了 `reasoning_content` 并发送给 API，API 将会忽略它们。

### 兼容性提示[​](#兼容性提示)

因思考模式下的工具调用过程中要求用户回传 `reasoning_content` 给 API，若您的代码中未正确回传 `reasoning_content`，API 会返回 400 报错。正确回传方法请您参考下面的样例代码。

### 样例代码[​](#样例代码-1)

下面是一个简单的在思考模式下进行工具调用的样例代码：

```
import os
import json
from openai import OpenAI

# The definition of the tools
tools = [
 {
 "type": "function",
 "function": {
 "name": "get_date",
 "description": "Get the current date",
 "parameters": { "type": "object", "properties": {} },
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
 "location": { "type": "string", "description": "The city name" },
 "date": { "type": "string", "description": "The date in format YYYY-mm-dd" },
 },
 "required": ["location", "date"]
 },
 }
 },
]

# The mocked version of the tool calls
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
 extra_body={ "thinking": { "type": "enabled" } }
 )
 messages.append(response.choices[0].message)
 reasoning_content = response.choices[0].message.reasoning_content
 content = response.choices[0].message.content
 tool_calls = response.choices[0].message.tool_calls
 print(f"Turn {turn}.{sub_turn}\n{reasoning_content=}\n{content=}\n{tool_calls=}")
 # If there is no tool calls, then the model should get a final answer and we need to stop the loop
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
 base_url=os.environ.get('DEEPSEEK_BASE_URL'),
)

# The user starts a question
turn = 1
messages = [{
 "role": "user",
 "content": "How's the weather in Hangzhou Tomorrow"
}]
run_turn(turn, messages)

# The user starts a new question
turn = 2
messages.append({
 "role": "user",
 "content": "How's the weather in Hangzhou Tomorrow"
})
# We recommended to clear the reasoning_content in history messages so as to save network bandwidth
clear_reasoning_content(messages)
run_turn(turn, messages)

```

在 Turn 1 的每个子请求中，都携带了该 Turn 下产生的 `reasoning_content` 给 API，从而让模型继续之前的思考。`response.choices[0].message` 携带了 `assistant` 消息的所有必要字段，包括 `content`、`reasoning_content`、`tool_calls`。简单起见，可以直接用如下代码将消息 append 到 messages 结尾：

```
messages.append(response.choices[0].message)

```

这行代码等价于：

```
messages.append({
 'role': 'assistant',
 'content': response.choices[0].message.content,
 'reasoning_content': response.choices[0].message.reasoning_content,
 'tool_calls': response.choices[0].message.tool_calls,
})

```

在 Turn 2 开始时，我们建议丢弃掉之前 Turn 中的 `reasoning_content` 来节省网络带宽：

```
clear_reasoning_content(messages)

```

该代码的样例输出如下：

```
Turn 1.1
reasoning_content="The user is asking about the weather in Hangzhou tomorrow. I need to get the current date first, then calculate tomorrow's date, and then call the weather API. Let me start by getting the current date."
content=''
tool_calls=[ChatCompletionMessageToolCall(id='call_00_Tcek83ZQ4fFb1RfPQnsPEE5w', function=Function(arguments='{}', name='get_date'), type='function', index=0)]
tool_result(get_date): 2025-12-01

Turn 1.2
reasoning_content='Today is December 1, 2025. Tomorrow is December 2, 2025. I need to format the date as YYYY-mm-dd: "2025-12-02". Now I can call get_weather with location Hangzhou and date 2025-12-02.'
content=''
tool_calls=[ChatCompletionMessageToolCall(id='call_00_V0Uwt4i63m5QnWRS1q1AO1tP', function=Function(arguments='{"location": "Hangzhou", "date": "2025-12-02"}', name='get_weather'), type='function', index=0)]
tool_result(get_weather): Cloudy 7~13°C

Turn 1.3
reasoning_content="I have the weather information: Cloudy with temperatures between 7 and 13°C. I should respond in a friendly, helpful manner. I'll mention that it's for tomorrow (December 2, 2025) and give the details. I can also ask if they need any other information. Let's craft the response."
content="Tomorrow (Tuesday, December 2, 2025) in Hangzhou will be **cloudy** with temperatures ranging from **7°C to 13°C**. \n\nIt might be a good idea to bring a light jacket if you're heading out. Is there anything else you'd like to know about the weather?"
tool_calls=None

Turn 2.1
reasoning_content="The user wants clothing advice for tomorrow based on the weather in Hangzhou. I know tomorrow's weather: cloudy, 7-13°C. That's cool but not freezing. I should suggest layered clothing, maybe a jacket, long pants, etc. I can also mention that since it's cloudy, an umbrella might not be needed unless there's rain chance, but the forecast didn't mention rain. I should be helpful and give specific suggestions. I can also ask if they have any specific activities planned to tailor the advice. Let me respond."
content="Based on tomorrow's forecast of **cloudy weather with temperatures between 7°C and 13°C** in Hangzhou, here are some clothing suggestions:\n\n**Recommended outfit:**\n- **Upper body:** A long-sleeve shirt or sweater, plus a light to medium jacket (like a fleece, windbreaker, or light coat)\n- **Lower body:** Long pants or jeans\n- **Footwear:** Closed-toe shoes or sneakers\n- **Optional:** A scarf or light hat for extra warmth, especially in the morning and evening\n\n**Why this works:**\n- The temperature range is cool but not freezing, so layering is key\n- Since it's cloudy but no rain mentioned, you likely won't need an umbrella\n- The jacket will help with the morning chill (7°C) and can be removed if you warm up during the day\n\n**If you have specific plans:**\n- For outdoor activities: Consider adding an extra layer\n- For indoor/office settings: The layered approach allows you to adjust comfortably\n\nWould you like more specific advice based on your planned activities?"
tool_calls=None

```

---

## 多轮对话

> 本指南将介绍如何使用 DeepSeek /chat/completions API 进行多轮对话。

- 

- API 指南
- 多轮对话

# 多轮对话

本指南将介绍如何使用 DeepSeek `/chat/completions` API 进行多轮对话。

DeepSeek `/chat/completions` API 是一个“无状态” API，即服务端不记录用户请求的上下文，用户在每次请求时，**需将之前所有对话历史拼接好后**，传递给对话 API。

下面的代码以 Python 语言，展示了如何进行上下文拼接，以实现多轮对话。

```
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

在**第一轮**请求时，传递给 API 的 `messages` 为：

```
[
 {"role": "user", "content": "What's the highest mountain in the world?"}
]

```

在**第二轮**请求时：

- 要将第一轮中模型的输出添加到 `messages` 末尾

- 将新的提问添加到 `messages` 末尾

最终传递给 API 的 `messages` 为：

```
[
 {"role": "user", "content": "What's the highest mountain in the world?"},
 {"role": "assistant", "content": "The highest mountain in the world is Mount Everest."},
 {"role": "user", "content": "What is the second?"}
]

```

---

## 对话前缀续写

> 对话前缀续写沿用 Chat Completion API，用户提供 assistant 开头的消息，来让模型补全其余的消息。

- 

- API 指南
- 对话前缀续写（Beta）
本页总览
# 对话前缀续写（Beta）

对话前缀续写沿用 [Chat Completion API](/zh-cn/api/create-chat-completion)，用户提供 assistant 开头的消息，来让模型补全其余的消息。

## 注意事项[​](#注意事项)

- 使用对话前缀续写时，用户需确保 `messages` 列表里最后一条消息的 `role` 为 `assistant`，并设置最后一条消息的 `prefix` 参数为 `True`。

- 用户需要设置 `base_url="https://api.deepseek.com/beta"` 来开启 Beta 功能。

## 样例代码[​](#样例代码)

下面给出了对话前缀续写的完整 Python 代码样例。在这个例子中，我们设置 `assistant` 开头的消息为 `"```python\n"` 来强制模型输出 python 代码，并设置 `stop` 参数为 `['```']` 来避免模型的额外解释。

```
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

---

## FIM补全

> 在 FIM (Fill In the Middle) 补全中，用户可以提供前缀和后缀（可选），模型来补全中间的内容。FIM 常用于内容续写、代码补全等场景。

- 

- API 指南
- FIM 补全（Beta）
本页总览
# FIM 补全（Beta）

在 [FIM (Fill In the Middle) 补全](/zh-cn/api/create-completion)中，用户可以提供前缀和后缀（可选），模型来补全中间的内容。FIM 常用于内容续写、代码补全等场景。

## 注意事项[​](#注意事项)

- 模型的最大补全长度为 4K。

- 用户需要设置 `base_url="https://api.deepseek.com/beta"` 来开启 Beta 功能。

## 样例代码[​](#样例代码)

下面给出了 FIM 补全的完整 Python 代码样例。在这个例子中，我们给出了计算斐波那契数列函数的开头和结尾，来让模型补全中间的内容。

```
from openai import OpenAI

client = OpenAI(
 api_key="<your api key>",
 base_url="https://api.deepseek.com/beta",
)

response = client.completions.create(
 model="deepseek-chat",
 prompt="def fib(a):",
 suffix=" return fib(a-1) + fib(a-2)",
 max_tokens=128
)
print(response.choices[0].text)

```

## 配置 Continue 代码补全插件[​](#配置-continue-代码补全插件)

[Continue](https://continue.dev) 是一款支持代码补全的 VSCode 插件，您可以参考[这篇文档](https://github.com/deepseek-ai/awesome-deepseek-integration/blob/main/docs/continue/README_cn.md)来配置 Continue 以使用代码补全功能。

---

## JSON Output

- 

- API 指南
- JSON Output
本页总览
# JSON Output

在很多场景下，用户需要让模型严格按照 JSON 格式来输出，以实现输出的结构化，便于后续逻辑进行解析。

DeepSeek 提供了 JSON Output 功能，来确保模型输出合法的 JSON 字符串。

## 注意事项[​](#注意事项)

- 设置 `response_format` 参数为 `{'type': 'json_object'}`。

- 用户传入的 system 或 user prompt 中必须含有 `json` 字样，并给出希望模型输出的 JSON 格式的样例，以指导模型来输出合法 JSON。

- 需要合理设置 `max_tokens` 参数，防止 JSON 字符串被中途截断。

- **在使用 JSON Output 功能时，API 有概率会返回空的 content。我们正在积极优化该问题，您可以尝试修改 prompt 以缓解此类问题。**

## 样例代码[​](#样例代码)

这里展示了使用 JSON Output 功能的完整 Python 代码：

```
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

messages = [{"role": "system", "content": system_prompt},
 {"role": "user", "content": user_prompt}]

response = client.chat.completions.create(
 model="deepseek-chat",
 messages=messages,
 response_format={
 'type': 'json_object'
 }
)

print(json.loads(response.choices[0].message.content))

```

模型将会输出：

```
{
 "question": "Which is the longest river in the world?",
 "answer": "The Nile River"
}

```

---

## Tool Calls

- 

- API 指南
- Tool Calls
本页总览
# Tool Calls

Tool Calls 让模型能够调用外部工具，来增强自身能力。

## 非思考模式[​](#非思考模式)

### 样例代码[​](#样例代码)

这里以获取用户当前位置的天气信息为例，展示了使用 Tool Calls 的完整 Python 代码。

Tool Calls 的具体 API 格式请参考[对话补全](/zh-cn/api/create-chat-completion/)文档。

```
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

messages.append({"role": "tool", "tool_call_id": tool.id, "content": "24℃"})
message = send_messages(messages)
print(f"Model>\t {message.content}")

```

这个例子的执行流程如下：

- 用户：询问现在的天气

- 模型：返回 function `get_weather({location: 'Hangzhou'})`

- 用户：调用 function `get_weather({location: 'Hangzhou'})`，并传给模型。

- 模型：返回自然语言，"The current temperature in Hangzhou is 24°C."

注：上述代码中 `get_weather` 函数功能需由用户提供，模型本身不执行具体函数。

## 思考模式[​](#思考模式)

从 DeepSeek-V3.2 开始，API 支持了思考模式下的工具调用能力，详见[思考模式](/zh-cn/guides/thinking_mode#%E5%B7%A5%E5%85%B7%E8%B0%83%E7%94%A8)。

## `strict` 模式（Beta）[​](#strict-模式beta)

在 `strict` 模式下，模型在输出 Function 调用时会严格遵循 Function 的 JSON Schema 的格式要求，以确保模型输出的 Function 符合用户的定义。在思考与非思考模式下的工具调用，均可使用 `strict` 模式。

要使用 `strict` 模式，需要：

- 用户需要设置 `base_url="https://api.deepseek.com/beta"` 来开启 Beta 功能

- 在传入的 `tools` 列表中，所有 `function` 均需设置 `strict` 属性为 `true`

- 服务端会对用户传入的 Function 的 JSON Schema 进行校验，如不符合规范，或遇到服务端不支持的 JSON Schema 类型，将返回错误信息

以下是 `strict` 模式下 tool 的定义样例：

```
{
 "type": "function",
 "function": {
 "name": "get_weather",
 "strict": true,
 "description": "Get weather of a location, the user should supply a location first.",
 "parameters": {
 "type": "object",
 "properties": {
 "location": {
 "type": "string",
 "description": "The city and state, e.g. San Francisco, CA",
 }
 },
 "required": ["location"],
 "additionalProperties": false
 }
 }
}

```

### `strict` 模式支持的 JSON Schema 类型[​](#strict-模式支持的-json-schema-类型)

- object

- string

- number

- integer

- boolean

- array

- enum

- anyOf

#### object 类型[​](#object-类型)

object 定义一个包含键值对的深层结构，其中 properties 定义了对象中每个键（属性）的 schema。**每个 `object` 的所有属性均需设置为 `required`，且 `object` 中 `additionalProperties` 属性必须为 `false`**。

示例：

```
{
 "type": "object",
 "properties": {
 "name": { "type": "string" },
 "age": { "type": "integer" }
 },
 "required": ["name", "age"],
 "additionalProperties": false
}

```

#### string 类型[​](#string-类型)

- 支持的参数：

pattern：使用正则表达式来约束字符串的格式

- format：使用预定义的常见格式进行校验，目前支持：

email：电子邮件地址

- hostname：主机名

- ipv4：IPv4 地址

- ipv6：IPv6 地址

- uuid：uuid

- 不支持的参数

minLength

- maxLength

示例：

```
{
 "type": "object",
 "properties": {
 "user_email": {
 "type": "string",
 "description": "The user's email address",
 "format": "email" 
 },
 "zip_code": {
 "type": "string",
 "description": "Six digit postal code",
 "pattern": "^\\d{6}$"
 }
 }
}

```

#### number/integer 类型[​](#numberinteger-类型)

- 支持的参数

const：固定数字为常数

- default：数字的默认值

- minimum：最小值

- maximum：最大值

- exclusiveMinimum：不小于

- exclusiveMaximum：不大于

- multipleOf：数字输出为这个值的倍数

示例：

```
{
 "type": "object",
 "properties": {
 "score": {
 "type": "integer",
 "description": "A number from 1-5, which represents your rating, the higher, the better",
 "minimum": 1,
 "maximum": 5
 }
 },
 "required": ["score"],
 "additionalProperties": false
}

```

#### array 类型[​](#array-类型)

- 不支持的参数

minItems

- maxItems

示例：

```
{
 "type": "object",
 "properties": {
 "keywords": {
 "type": "array",
 "description": "Five keywords of the article, sorted by importance",
 "items": {
 "type": "string",
 "description": "A concise and accurate keyword or phrase."
 }
 }
 },
 "required": ["keywords"],
 "additionalProperties": false
}

```

#### enum[​](#enum)

enum 可以确保输出是预期的几个选项之一，例如在订单状态的场景下，只能是有限几个状态之一。

样例：

```
{
 "type": "object",
 "properties": {
 "order_status": {
 "type": "string",
 "description": "Ordering status",
 "enum": ["pending", "processing", "shipped", "cancelled"]
 }
 }
}

```

#### anyOf[​](#anyof)

匹配所提供的多个 schema 中的任意一个，可以处理可能具有多种有效格式的字段，例如用户的账户可能是邮箱或者手机号中的一个：

```
{
 "type": "object",
 "properties": {
 "account": {
 "anyOf": [
 { "type": "string", "format": "email", "description": "可以是电子邮件地址" },
 { "type": "string", "pattern": "^\\d{11}$", "description": "或11位手机号码" }
 ]
 }
 }
}

```

#### $ref 和 $def[​](#ref-和-def)

可以使用 $def 定义模块，再用 $ref 引用以减少模式的重复和模块化，此外还可以单独使用 $ref 定义递归结构。

```
{
 "type": "object",
 "properties": {
 "report_date": {
 "type": "string",
 "description": "The date when the report was published"
 },
 "authors": {
 "type": "array",
 "description": "The authors of the report",
 "items": {
 "$ref": "#/$def/author"
 }
 }
 },
 "required": ["report_date", "authors"],
 "additionalProperties": false,
 "$def": {
 "authors": {
 "type": "object",
 "properties": {
 "name": {
 "type": "string",
 "description": "author's name"
 },
 "institution": {
 "type": "string",
 "description": "author's institution"
 },
 "email": {
 "type": "string",
 "format": "email",
 "description": "author's email"
 }
 },
 "additionalProperties": false,
 "required": ["name", "institution", "email"]
 }
 }
}

```

---

## 上下文硬盘缓存

> DeepSeek API 上下文硬盘缓存技术对所有用户默认开启，用户无需修改代码即可享用。

- 

- API 指南
- 上下文硬盘缓存
本页总览
# 上下文硬盘缓存

DeepSeek API [上下文硬盘缓存技术](/zh-cn/news/news0802)对所有用户默认开启，用户无需修改代码即可享用。

用户的每一个请求都会触发硬盘缓存的构建。若后续请求与之前的请求在前缀上存在重复，则重复部分只需要从缓存中拉取，计入“缓存命中”。

注意：两个请求间，只有重复的**前缀**部分才能触发“缓存命中”，详间下面的例子。

### 例一：长文本问答[​](#例一长文本问答)

**第一次请求**

```
messages: [
 {"role": "system", "content": "你是一位资深的财报分析师..."}
 {"role": "user", "content": "<财报内容>\n\n请总结一下这份财报的关键信息。"}
]

```

**第二次请求**

```
messages: [
 {"role": "system", "content": "你是一位资深的财报分析师..."}
 {"role": "user", "content": "<财报内容>\n\n请分析一下这份财报的盈利情况。"}
]

```

在上例中，两次请求都有相同的**前缀**，即 `system` 消息 + `user` 消息中的 `<财报内容>`。在第二次请求时，这部分前缀会计入“缓存命中”。

### 例二：多轮对话[​](#例二多轮对话)

**第一次请求**

```
messages: [
 {"role": "system", "content": "你是一位乐于助人的助手"},
 {"role": "user", "content": "中国的首都是哪里？"}
]

```

**第二次请求**

```
messages: [
 {"role": "system", "content": "你是一位乐于助人的助手"},
 {"role": "user", "content": "中国的首都是哪里？"},
 {"role": "assistant", "content": "中国的首都是北京。"},
 {"role": "user", "content": "美国的首都是哪里？"}
]

```

在上例中，第二次请求可以复用第一次请求**开头**的 `system` 消息和 `user` 消息，这部分会计入“缓存命中”。

### 例三：使用 Few-shot 学习[​](#例三使用-few-shot-学习)

在实际应用中，用户可以通过 Few-shot 学习的方式，来提升模型的输出效果。所谓 Few-shot 学习，是指在请求中提供一些示例，让模型学习到特定的模式。由于 Few-shot 一般提供相同的上下文前缀，在硬盘缓存的加持下，Few-shot 的费用显著降低。

**第一次请求**

```
messages: [ 
 {"role": "system", "content": "你是一位历史学专家，用户将提供一系列问题，你的回答应当简明扼要，并以`Answer:`开头"},
 {"role": "user", "content": "请问秦始皇统一六国是在哪一年？"},
 {"role": "assistant", "content": "Answer:公元前221年"},
 {"role": "user", "content": "请问汉朝的建立者是谁？"},
 {"role": "assistant", "content": "Answer:刘邦"},
 {"role": "user", "content": "请问唐朝最后一任皇帝是谁"},
 {"role": "assistant", "content": "Answer:李柷"},
 {"role": "user", "content": "请问明朝的开国皇帝是谁？"},
 {"role": "assistant", "content": "Answer:朱元璋"},
 {"role": "user", "content": "请问清朝的开国皇帝是谁？"}
]

```

**第二次请求**

```
messages: [ 
 {"role": "system", "content": "你是一位历史学专家，用户将提供一系列问题，你的回答应当简明扼要，并以`Answer:`开头"},
 {"role": "user", "content": "请问秦始皇统一六国是在哪一年？"},
 {"role": "assistant", "content": "Answer:公元前221年"},
 {"role": "user", "content": "请问汉朝的建立者是谁？"},
 {"role": "assistant", "content": "Answer:刘邦"},
 {"role": "user", "content": "请问唐朝最后一任皇帝是谁"},
 {"role": "assistant", "content": "Answer:李柷"},
 {"role": "user", "content": "请问明朝的开国皇帝是谁？"},
 {"role": "assistant", "content": "Answer:朱元璋"},
 {"role": "user", "content": "请问商朝是什么时候灭亡的"}, 
]

```

在上例中，使用了 4-shots。两次请求只有最后一个问题不一样，第二次请求可以复用第一次请求中前 4 轮对话的内容，这部分会计入“缓存命中”。

## 查看缓存命中情况[​](#查看缓存命中情况)

在 DeepSeek API 的返回中，我们在 `usage` 字段中增加了两个字段，来反映请求的缓存命中情况：

- 

`prompt_cache_hit_tokens`：本次请求的输入中，缓存命中的 tokens 数（0.1 元 / 百万 tokens）

- 

`prompt_cache_miss_tokens`：本次请求的输入中，缓存未命中的 tokens 数（1 元 / 百万 tokens）

## 硬盘缓存与输出随机性[​](#硬盘缓存与输出随机性)

硬盘缓存只匹配到用户输入的前缀部分，输出仍然是通过计算推理得到的，仍然受到 temperature 等参数的影响，从而引入随机性。其输出效果与不使用硬盘缓存相同。

## 其它说明[​](#其它说明)

- 

缓存系统以 64 tokens 为一个存储单元，不足 64 tokens 的内容不会被缓存

- 

缓存系统是“尽力而为”，不保证 100% 缓存命中

- 

缓存构建耗时为秒级。缓存不再使用后会自动被清空，时间一般为几个小时到几天
