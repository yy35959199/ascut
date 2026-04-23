# 智能层设计（Layer 2）— MVP 现行版

> 本文档是 Layer 2 在 MVP 阶段的唯一实现依据，只记录当前确认的设计，不包含历史方案与演进过程。  
> 架构愿景见 [AutoSmartCut.md](AutoSmartCut.md)，MVP 全局规划见 [AutoSmartCut-MVP.md](AutoSmartCut-MVP.md)。

---

## 目录

1. [文档定位](#1-文档定位)
2. [术语与命名约定](#2-术语与命名约定)
3. [MVP 范围与边界](#3-mvp-范围与边界)
4. [数据模型（Layer 2 相关）](#4-数据模型layer-2-相关)
5. [2a 理解子阶段](#5-2a-理解子阶段)
6. [2b 决策子阶段](#6-2b-决策子阶段)
7. [2c 审核子阶段（MVP 两阶段）](#7-2c-审核子阶段mvp-两阶段)
8. [2d 人工子阶段（MVP）](#8-2d-人工子阶段mvp)
9. [Pipeline 时序](#9-pipeline-时序)
10. [与 Layer 1 / Layer 3 的契约](#10-与-layer-1--layer-3-的契约)
11. [层间清单与 EDL 归属](#11-层间清单与-edl-归属)

---

## 1. 文档定位

- 本文只覆盖 Layer 2（智能层）在 MVP 阶段的实现设计。
- 上游 Layer 1、下游 Layer 3 仅描述必要接口契约，不展开实现细节。
- 本文不包含多周目、循环控制、预算守卫、结构化反馈回流等 MVP 之后能力。

---

## 2. 术语与命名约定

为避免历史文档中的命名歧义，本文统一使用以下名称：

- **理解分块（2a 产物）**：`comprehension.outline_blocks[]`  
每个块包含 index 范围与块总结（可选块标题）。
- **决策结果（2b/2d 产物）**：`current.keep_mask[]`（与 `**tokens[]`** 等长并按 `index` 对齐，亦即与 `**annotations[]**` 条数相同）  
用于表示每条句面的 keep/cut 建议与人工覆盖后的最终有效决策。
- **主坐标系统**：`annotation.index`  
Layer 2 的语义处理坐标统一用 index，不用时间戳做主输入。

---

## 3. MVP 范围与边界

### 3.1 MVP 内

- 单周目 pipeline：`2a -> 2b ↔ 2c(审核循环) -> 2d -> 定稿 keep_mask（对外交付）`。
- 2a：**两次 LLM 调用（R1 + R2）** + **一次程序步骤**（按 R2 的 `corrections` 生成 `cleaned_annotations`，不调用 LLM）。
- 2b 固定一次 LLM 调用（首次）；2c 审核未通过时可修正重跑 2b（注入 `review_fixes`）。
- 2c：**已实现真实 LLM 审核**（`two_c_max_review_rounds >= 1` 时）。单次 LLM 调用，两阶段输出：先生成 checklist（基于 goal + outline_blocks），再逐条对照 keep_mask 判断。verdict 由程序根据 must 项通过率计算。`two_c_max_review_rounds = 0` 时退化为占位透传（向后兼容）。
- 2b ↔ 2c 内循环：2c 返回 `fix_decision` 时，提取 `fix_instructions`（含具体应保留的 index），注入 2b prompt 重跑；最多循环 `two_c_max_review_rounds` 轮（默认 1），达上限强制 pass 交 2d 人工兜底。
- 2d 只提供手动勾选（index 级）与确认；**智能层（含 2d）对外的最终产物是定稿 `keep_mask`**，不产出 EDL。

### 3.2 MVP 外

- 不做多周目（不从 2d 回流 2a/2b）。
- 不做结构化反馈输入框 1/2/3/4。
- 不做 2c → 2a 外循环（`fix_checklist` 路径；当前仅 `fix_decision` → 2b 内循环）。
- 不做 Token 使用量与预算策略（统一移至 `after-mvp-todo.md`）。

---

## 4. 数据模型（Layer 2 相关）

### 4.1 输入字段

- **磁盘**：智能层从 `**timeline_manifest.json`** 读取清单（至少含非空 `**annotations[]**`、可选 `goal` 等）。
- `manifest.goal`（内存 `manifest_dict`）：用户剪辑目标。
- `**manifest.tokens[]`（必需，仅内存）**：由 `annotation_tokens.tokens_from_annotations(annotations)` 生成稠密 `tokens`（每项仅 `index`、`text`），**不落盘**。时间轴 `**t_start`/`t_end`/`gap_after` 不进入 LLM Prompt**，留在同清单的 `**annotations[]`**，供 **Layer 3** 与执行层纯函数使用。
- **与 Layer 1 的关系**：L1 已把句级结果写入清单 `**annotations[]`**；L2 **不**再依赖独立 `layer2_input.json`。
- **LLM 句面**：2a 的 R1/R2 **只消费 `tokens[]` + `goal`**，不把时间字段塞进 Prompt。

### 4.2 2a 写入字段（`manifest.comprehension`）

- `purpose`：最终主旨。
- `outline_blocks[]`：分块结果（index 范围 + 块总结）。
- `cleaned_annotations[]`：虚拟消歧文本（`annotation_index` + `cleaned_content`，字段名沿用 MVP），
**稠密全量**，与 `**tokens[]`** 等长且按 `index` 对齐。

### 4.3 2b 写入字段

- `keep_mask[]`：与 `**tokens[]**` 等长；每条均为 `keep=true|false`（**仅布尔**）。MVP **不使用** `keep: null`；句间静音由 Layer1 的 `gap_after` 表达，**无**独立 `type=silence` 标注行。
- `checklist_coverage[]`：MVP 阶段作为预留字段，可为空。

### 4.4 2c 写入字段（`manifest.review_report`）

2c 写入 `manifest_dict["review_report"]`，最终落盘于 `current.review_report`：

```json
{
  "round": 0,
  "verdict": "pass",
  "checklist": [
    {"item": "是否保留了关于X的核心解释", "source": "goal", "priority": "must"}
  ],
  "judgments": [
    {"checklist_index": 0, "pass": true, "evidence_indices": [12, 15], "note": "..."}
  ],
  "fix_instructions": [],
  "must_pass_rate": "3/3",
  "token_spent": 0
}
```

- `two_c_max_review_rounds = 0` 时：写入占位报告（`verdict="pass"`，`checklist`/`judgments` 为空数组）。
- `two_c_max_review_rounds >= 1` 时：真实 LLM 审核，`verdict` 由程序根据 must 项通过率计算（不由 LLM 输出）。

### 4.5 2d 写入字段

- `human_feedback_history[]`：MVP 仅记录手动覆盖操作与最终确认（不含自然语言反馈）。
- **定稿 `keep_mask`**：用户确认后，将 `effective_keep = merge(keep_mask, overrides)` 作为**智能层最终输出**写回清单 `**current.keep_mask`**（与 `**tokens[]**` / `**annotations[]**` 等长、按 `index` 对齐**）。
- **不在 Layer 2 生成或持久化 `edl[]`**：剪辑决策表（EDL）**不属于**智能层产出，见 [§11](#11-层间清单与-edl-归属)。

### 4.6 2a 中间产物（不持久化）

以下字段**主要存在于单次 `run_2a` 的内存中**；其中 `**corrections`** 等按现行实现可落入 `**current.comprehension**` 以便审计与重算，**不**单独要求「整段 R1 粗结果」落盘：

- R1：`purpose_rough`、`outline_blocks_rough`、`candidate_misrecognitions`
- R2：`corrections`（唯一错词替换映射；结构见 §5）

R2 结束后可丢弃 R1 粗结果等中间结构；**不**持久化 `symbol_table` / `symbol_table_candidates` 等别名字段。

---

## 5. 2a 理解子阶段

### 5.1 执行方式

1. **R1（LLM）**：粗主旨 + 粗分块 + 疑似错词候选表。
2. **R2（LLM）**：精化主旨 + 精化分块 + **错词唯一替换表** `corrections`。
3. **程序（非 LLM）**：根据 `corrections` 在 `**tokens[].text` 的只读映射/副本**上做替换，
  再回填为**稠密全量** `**comprehension.cleaned_annotations[]`**；
   **不**改写清单内 `annotations[].content`；**不**修改已载入的 `**tokens[]` 原文**（消歧结果仅存在于内存 `comprehension`，落盘策略见 `manifest_io.strip_volatile_fields`）。

LLM 调用走 `autosmartcut.intelligence_llm`（见 **§5.1.1**）。R1 为单轮；R2 与 R1 为**同一对话前缀上的第二跳**（真多轮，利于前缀缓存命中），而非两次互不关联的单轮请求。

#### 5.1.1 LLM 封装与多轮契约（现行实现）


| 能力           | 入口                                             | 说明                                                                                                             |
| ------------ | ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| 单轮结构化        | `call_llm_structured` / `call_once_structured` | `system` + `user`（含 JSON Schema 格式说明）；非流式                                                                      |
| R1 单轮 + 原文快照 | `call_once_structured_with_raw_content`        | 返回 `StructuredLLMResult`：`data`、`assistant_content`（模型返回的 JSON 字符串）、`usage`、`request_messages`（深拷贝，供 R2 前缀）    |
| 多轮下一跳        | `call_turn_structured(messages, schema, …)`    | 对 `messages` 先 `sanitize_messages_for_api`（去掉 `reasoning_content`），再对**最后一条** `role=user` 追加 JSON 格式说明后请求      |
| 拼接 R1→R2     | `prepare_next_turn_messages`                   | 在 `request_messages` 后追加 `assistant`（仅 `content`）与 R2 的 `user`（由 `intelligence_2a._build_r2_user_followup` 构造） |


思考模式与采样：

- 模块内常量 `**ENABLE_REASONING_R1`** / `**ENABLE_REASONING_R2**`（默认 `False`）分别控制 R1/R2 是否启用 reasoner。
- 思考模式下 `**_call_api` 不传 `temperature**`（与 DeepSeek 文档一致）；若配置为 `deepseek-chat` 且开启思考，则通过 `extra_body={"thinking":{"type":"enabled"}}`。
- 跨轮**不得**把历史轮的 `reasoning_content` 拼进 `messages`（由 `sanitize_messages_for_api` 保证）。

2b 仍为**单轮**调用，使用 `call_llm_structured`（与 `call_once_structured` 等价）。

输出校验使用 `**jsonschema.Draft202012Validator`**（实例与 schema 合法性）。

### 5.2 R1 输出（中间态，仅内存）

- `purpose_rough`：粗糙主旨。
- `outline_blocks_rough[]`：草稿分块，`start_index` / `end_index` / `topic`（或等价主题字段）。
- `candidate_misrecognitions[]`：疑似 ASR 误识，每条含 `annotation_index`（句子 index）、`wrong`（原文错误子串）、`suggestions[]`（候选正确写法）。

### 5.3 R2 输出（中间态，仅内存）

- `purpose`：精化主旨。
- `outline_blocks[]`：最终分块，每项含 `start_index` / `end_index` / `**summary`**（供 2b Prompt 使用；若模型输出 `topic`，实现层映射为 `summary`）。
- `corrections[]`：唯一替换列表，每项含 `index`、`old`（原文错误子串）、`nth`（该子串在原句中第几次出现，1-based）、`new`（替换词）。

### 5.4 程序步骤输出（写入 `manifest.comprehension`）

持久化字段仅三项（与 §4.2 一致）：

- `purpose`（来自 R2）
- `outline_blocks[]`（来自 R2，字段名为 `summary`）
- `cleaned_annotations[]`：**仅**由程序根据 `corrections` 生成；为稠密全量列表，
每条均为 `{annotation_index, cleaned_content}`。未发生纠错的句子，
`cleaned_content` 与对应 `**tokens[].text`** 相同。

### 5.5 输入与不变量

- **R1 文本输入**：`manifest["tokens"]`（内存稠密句面）+ `goal`。
- **R2 文本输入**：在同一会话中，上一条 `**assistant`** 消息为 R1 的完整 JSON（含 `purpose_rough`、`outline_blocks_rough`、`candidate_misrecognitions`）；本回合 `**user**` 不再重复粘贴上述字段全文，而是显式引用「上一轮 JSON」，并仍附带**完整句面列表**（`[index] text`）及候选条目的**原句核对**（供 `corrections` 锚定）。语义与旧版「把 R1 输出塞进单条 Prompt」等价，但前缀与 R1 的 `system`+首条 `user` 一致，利于上下文缓存。
- **清单 `annotations[].content` 与内存 `tokens[].text` 不被覆盖**；消歧仅通过 `cleaned_annotations[]` 旁路表达（Append-only 语义）。

---

## 6. 2b 决策子阶段

### 6.1 输入（固定）

- `comprehension.cleaned_annotations[]`（稠密全量，与 `**tokens[]`** 等长对齐）
- `comprehension.purpose`
- `comprehension.outline_blocks[]`（块 index 范围 + 块总结）
- `**manifest.tokens[]**`：构造 Prompt 中的全量句面列表；**不**把 `annotations[]` 的时间字段塞进 LLM（时间轴在同清单 `annotations[]`，由 L3 消费）。

### 6.2 输出

- `keep_mask[]`（长度必须等于 `len(tokens)`）
- `checklist_coverage[]`（MVP 预留）

### 6.3 校验与重试

- 必须校验 `keep_mask` 的长度、索引对齐、类型约束。
- 失败时整体重试，最多 3 次。

---

## 7. 2c 审核子阶段（现行实现）

### 7.1 设计原理

2c 是 2b 决策的**结构化验证器**，用不同的认知视角检验 2b 的输出：2b 是正向逐句决策（「这句该不该留」），2c 是逆向验证（「删掉这些之后，剩下的是否满足用户目标」）。

核心机制：在单次 LLM 调用中，先将模糊的 goal 分解为一组可判真假的 checklist 条目，再逐条对照 keep_mask 做布尔判断。verdict 由程序根据 must 项通过率计算，不由 LLM 直接输出。

**为什么需要 checklist**：LLM 对模糊目标做 pass/not-pass 的二值判断不稳定（采样随机性）。将 goal 分解为离散布尔条件，每条判断空间远小于原始 goal 的判断空间。

**为什么在同一次调用内生成 checklist 并逐条判断**：两阶段是因果推理链而非独立任务——先生成的 checklist token 直接参与后续判断的注意力计算。拆成两次调用会引入两层独立采样的随机性叠加，且第二次调用需要重新「理解」第一次生成的 checklist 文本。

**为什么 verdict 由程序计算**：LLM 生成 verdict 时可能与自己的 judgments 矛盾（3 条 must 未通过但仍输出 pass）。程序计算是确定性的，消除了这一层随机性。阈值可配置（`two_c_must_pass_rate`），不需要改 prompt 就能调整审核严格度。

### 7.2 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `two_c_max_review_rounds` | `1` | 最大修正轮次。`0`=占位透传（不调 LLM），`1`=审核+最多 1 轮修正，`2`=最多 2 轮修正 |
| `two_c_must_pass_rate` | `1.0` | must 项通过率阈值。`1.0`=全部 must 必须通过才 pass |

### 7.3 LLM 调用

- 单次调用，使用 `call_llm_structured`。
- `enable_reasoning = True`（需要逐条推理）。
- `temperature = 0.2`（审核需要确定性）。

### 7.4 Prompt 结构（五个区段）

1. **阶段定位**：声明当前为 2c 审核层，任务是验证而非重新决策。
2. **上下文注入**：goal + purpose + outline_blocks 摘要。
3. **审核材料**：按 outline_block 分组展示句面，每句标注 `[✓]`（保留）或 `[✗]`（删除），让模型看到完整的决策结果。
4. **第一步任务指令（生成 checklist）**：强制声明「此步必须优先完成，在 checklist 数组完整输出之前，禁止开始第二步」。checklist 基于 goal（首要）+ outline_blocks（辅助）生成，每条标注 `must`/`optional` 优先级和来源（`goal`/`block_N`/`structural`）。
5. **第二步任务指令（逐条判断）**：强制声明「禁止依赖记忆或整体印象，必须从头重新逐句阅读原文」。每条判断必须给出 `evidence_indices`（具体句子 index），不允许无证据的判断。

### 7.5 输出 Schema

```json
{
  "checklist": [
    {"item": "str", "source": "goal|block_N|structural", "priority": "must|optional"}
  ],
  "judgments": [
    {"checklist_index": 0, "pass": true, "evidence_indices": [12, 15], "note": "str"}
  ]
}
```

**不在 schema 里放 verdict**，由程序计算。

### 7.6 程序层后处理

1. **verdict 计算**：统计 must 项通过率，与 `two_c_must_pass_rate` 比较。
2. **fix_instructions 提取**：从未通过的 must 项中提取 `{requirement, missing_indices, note}`。仅提取 `evidence_indices` 非空的项——没有具体 index 的修正指令对 2b 无意义。
3. **边界情况**：若 `fix_instructions` 为空但 verdict 为 `fix_decision`，强制改为 `pass`（无法给出有效修正指令时，交给 2d 人工兜底）。

### 7.7 2b ↔ 2c 内循环

编排层（`intelligence.py`）控制循环：

```
for review_round in range(max_review_rounds + 1):
    2b（首次无 fixes，修正重跑时注入 review_fixes）
    2c 审核
    if verdict == "pass": break
    if review_round < max_review_rounds:
        提取 fix_instructions → 下一轮 2b 注入
    else:
        强制 pass → 2d
```

### 7.8 2b 修正重跑时的 prompt 注入

2c 返回 `fix_decision` 时，`fix_instructions` 注入 2b prompt 的「阶段定位」之后、「内容主旨」之前：

- **single 模式**：全量注入所有 fix_instructions。
- **chunked 模式**：按子块 index 范围过滤，仅注入与本子块相关的修正项。无相关修正项的子块不注入该区段。

注入内容示例：
```
【审核修正指令（本次为 2c 审核后的修正重跑）】
上一轮决策存在以下问题，本轮须优先修正。对于下列指出应保留的 index，
除非该句是纯语气词或与前后句完全重复，否则必须改为 keep=true：

1. 未满足条件：「是否保留了三个实验的对比结论」
   应保留但被删除的句子：index 43, 45, 47
   说明：第三个实验的结论部分被整段删除
```

### 7.9 LLM 调用次数总结

| 场景 | 调用次数 |
|------|---------|
| 2c 关闭（`max_rounds=0`） | 2a×2 + 2b×1 = 3 次 |
| 2c 开启，一次通过 | 2a×2 + 2b×1 + 2c×1 = 4 次 |
| 2c 开启，修正 1 轮 | 2a×2 + 2b×1 + 2c×1 + 2b×1 + 2c×1 = 6 次 |

---

## 8. 2d 人工子阶段（MVP）

### 8.1 仅保留手动勾选

- 展示当前决策结果（可按 index 或按块辅助展示）。
- 用户手动切换指定 index 的 keep/cut。
- 变更记录写入 `overrides` delta，不原地改写模型原始 `keep_mask`。

### 8.2 合并与确认

- 有效决策：`effective_keep = merge(keep_mask, overrides)`。
- 用户确认后：将 `**effective_keep` 固化为定稿 `keep_mask`**，写回 `**timeline_manifest.json`** 的 `**current.keep_mask**`，作为交给执行层的决策载体；**不在此处合成 `edl[]`**。

### 8.3 明确边界

- 2d 覆盖的是 2b 决策输出。
- MVP 不支持在 2d 提交反馈回流 2a/2b。

---

## 9. Pipeline 时序

```mermaid
flowchart LR
  step2aR1[2a_R1_LLM] --> step2aR2[2a_R2_LLM]
  step2aR2 --> step2aApply[apply_corrections]
  step2aApply --> step2b[2b 决策]
  step2b --> step2c{2c 审核}
  step2c -->|pass| step2d[2d 人工]
  step2c -->|fix_decision\n且未达上限| step2b_fix[2b 修正重跑\n注入 review_fixes]
  step2b_fix --> step2c
  step2c -->|达到 max_rounds\n强制 pass| step2d
  step2d --> stepKeep[persist current.keep_mask]
```

**循环控制**：`two_c_max_review_rounds`（默认 1）控制 2b↔2c 最大修正轮次。`= 0` 时 2c 为占位透传，pipeline 退化为线性序列。



---

## 10. 与 Layer 1 / Layer 3 的契约

- Layer 2 与 Layer 1 对齐依赖 **句级 `index` 稳定一致**（内存 `tokens` 与清单 `annotations` 逐条对应）。
- **智能层（含 2d）对执行层的交付物是定稿 `current.keep_mask`**（与 `**tokens**` / `**annotations[]**` 等长）；**不是** `edl[]`。
- 识别层输出中带 `**index`、`t_start`、`t_end` 与 `gap_after`** 的句级序列，构成「时间–index 映射」；执行层用其与 `keep_mask` 合并后**在内部**得到连续时间上的 keep 区间，再**在执行层内部**合成 EDL 并驱动剪切。
- Layer 2 语义决策主坐标为 `index`；**时间换算与 EDL 合成**属于执行层职责。
- **执行层（MVP）**在合成浮点保留区间后，还可选用 **Silero VAD 切点吸附（Snap）** 微调入/出点（不写回源文件）；与 L2 契约无关，详见 [AutoSmartCut-MVP.md](AutoSmartCut-MVP.md) 中 `[execution]` 与 `--no-vad-snap`。

---

## 11. 层间清单与 EDL 归属

- **解耦方式**：识别层、智能层、执行层通过**同一份** `**timeline_manifest.json`** 约定字段路径交接（字段级以 [AutoSmartCut-MVP-Mini.md](AutoSmartCut-MVP-Mini.md) 与 [AutoSmartCut-MVP.md](AutoSmartCut-MVP.md) 为准）。
- **识别层写入**：`annotations[]`（含 `index`、`t_start`、`t_end`、`gap_after` 等），即执行层所需的「时间–index 映射」来源。
- **智能层写入**：`current.comprehension` 与 `**current.keep_mask`**（经 2b 与 2d `overrides` 合并后的定稿；MVP 默认无 overrides 时即 2b 输出）。
- **EDL**：由**执行层**读取清单内 `**annotations[]` + `current.keep_mask`** 后，在**执行层内部**合并计算得到；**不作为 Layer 2 的对外落盘字段**。

---

*文档版本：0.6.0*  
*状态：MVP 现行实现依据（单清单 + 内存 `tokens[]`；2a R1+R2 真多轮；2c 真实 LLM 审核 + 2b↔2c 内循环；2026-04-23 与仓库对齐）*  