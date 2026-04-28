# 智能层设计（Layer 2）

> 最后更新：2026-04-28  
> 本文档与当前代码一致：L2 节点读取 **`annotations[]`**（及派生 `tokens[]`），**不**以 `annotations_l1a` 为编排契约。

---

## 目录

1. [概述](#1-概述)
2. [数据契约](#2-数据契约)
3. [2a 理解子阶段](#3-2a-理解子阶段)
4. [2b 决策子阶段](#4-2b-决策子阶段)
5. [2c 审核子阶段](#5-2c-审核子阶段)
6. [2d 人工子阶段](#6-2d-人工子阶段)
7. [与 L1 / L3 的契约](#7-与-l1--l3-的契约)

---

## 1. 概述

智能层在产品中分为 **2a → 2b ↔ 2c → 2d**；编排上对应节点 `l2a_comprehension`、`l2b_decision`、`l2c_review`、`l2d_human`。

```
2a 理解 → 2b 决策 ↔ 2c 审核（内循环）→ 2d 人工定稿
                        │
            REFLOW_2A ──┤（F1 / F2）
            REFLOW_2B ──┘（F3）
```

### 1.1 LLM 调用次数（典型）

| 场景 | 次数 | 说明 |
|------|------|------|
| `two_c_max_review_rounds = 0` | 3 | 2a×2 + 2b×1（2c 占位透传，不调 LLM） |
| `two_c_max_review_rounds ≥ 1`，一次通过 | 4 | 2a×2 + 2b×1 + 2c×1 |
| 2c 要求修正且未达上限 | +2/轮 | 额外 2b×1 + 2c×1 |
| 最后一次仍 `fix_decision` | +0 LLM（2b） | 调度器注入 `force_pass=True`，2b 本地全 `keep` |
| 每次 **REFLOW_2A** | +2（2a）+1（2b）+（2c 开则 +1） | 重跑 2a 全量两轮 + 下游 |
| 每次 **REFLOW_2B** | +1（2b）+（2c 开则 +1） | 重跑 2b + 审核 |

---

## 2. 数据契约

### 2.1 各子阶段与 manifest 的关系

实现中节点多写入 manifest **顶层**键；`PipelineSession` 在节点成功后将下列键同步到 `manifest["current"]`：`keep_mask`、`comprehension`、`review_report`、`human_feedback_history`、`l2d_completed`。

| 子阶段 | 主要读取（逻辑上） | 主要写入 |
|--------|-------------------|----------|
| **2a** | `annotations[]` → `tokens[]`（若缺则由节点派生）、`goal` | `comprehension` |
| **2b** | `comprehension`、`annotations[]` → `tokens[]` | `keep_mask` |
| **2c** | `keep_mask`、`comprehension`、`annotations[]` → `tokens[]`、`goal` | `review_report` |
| **2d** | `keep_mask`、`review_report`、`comprehension` | `human_feedback_history`、`l2d_completed`；就地更新 `keep_mask` |

### 2.2 `tokens[]` 与 `cleaned_annotations[]`

- **`tokens[]`**：`annotation_tokens.tokens_from_annotations(annotations)`，每项 `index` + `text`。节点常写入 **`manifest["tokens"]`**，在 `PipelineSession` 保存清单时**可能落盘**；与 `annotations[]` 条数一致、`index` 对齐。
- **`cleaned_annotations[]`**：2a 内由 R2 的 `corrections` 经过程序稠密化，写入 **`manifest["comprehension"]["cleaned_annotations"]`**（见 `intelligence_2a.run_2a_comprehension`）。  
  发布或精简清单时，可调用 `manifest_io.strip_volatile_fields()`：移除 `current.tokens`、`current.l2_checkpoints`、`current` 内 `comprehension.cleaned_annotations` 等；**不**移除顶层 `manifest["tokens"]`（需无 `tokens` 时请自行 `pop` 或另行剥离，按调用方策略）。

---

## 3. 2a 理解子阶段

### 3.1 流程

```
R1（LLM，结构化）→ R2（LLM，真多轮续写）→ 程序稠密化 cleaned_annotations
```

- **R1**（仅内存直到写入 checkpoint/清单）：`purpose_rough`、`outline_blocks_rough`、`candidate_misrecognitions`。
- **R2**：`purpose`、`outline_blocks`、`corrections`（`index` / `old` / `nth` / `new`）。
- **程序**：在只读句面上应用纠错规则，生成与 `tokens` 等长的 **`cleaned_annotations`**，写入 `manifest["comprehension"]`。不修改 `annotations[].content` 与 `tokens[].text`。

### 3.2 LLM API（真多轮）

| 步骤 | 入口 | 作用 |
|------|------|------|
| R1 | `call_once_structured_with_raw_content` | 单轮 + 返回 R1 assistant JSON 供前缀 |
| 拼 R2 | `prepare_next_turn_messages` | 接上 R1 assistant，再追加 R2 user |
| R2 | `call_turn_structured` | 多轮下一跳；`sanitize_messages_for_api` 清理不可转发字段 |

### 3.3 持久化形态

运行期 `comprehension` 至少包含：`purpose`、`outline_blocks`、`cleaned_annotations`。  
**不**将 `corrections` 作为与实现对齐的独立长期字段来承诺（R2 输出由程序消费并物化为 `cleaned_annotations`）。

---

## 4. 2b 决策子阶段

### 4.1 输入

- `comprehension.purpose`、`outline_blocks[]`
- `comprehension.cleaned_annotations[]`（稠密句面）
- `tokens[]`（用于 Prompt 装配）
- 时间字段不进 LLM Prompt，留在 `annotations[]` 供 L3。

### 4.2 输出

- `keep_mask[]`：长度 `len(tokens)`；每项 `{"index": i, "keep": bool}`；`index` 连续对齐。

### 4.3 `single` / `block`

由 `[intelligence] two_b_mode` 控制，CLI `--two-b-mode` 可覆盖。

### 4.4 重试与审核回流

- 2b 失败重试策略见 `intelligence_2b` 实现。
- 2c `fix_decision` 时，调度器向 2b 注入 `review_fixes`；用尽轮次后 `force_pass=True`，2b 跳过 LLM 全保留。

---

## 5. 2c 审核子阶段

### 5.1 原理

- 单次 LLM 调用内：**checklist → judgments**。
- **`verdict` 由程序**根据 must 项通过率与 `two_c_must_pass_rate` 计算。
- `two_c_max_review_rounds == 0`：占位透传，自动生成 `pass` 报告，**不调 LLM**。

### 5.2 输出 Schema（LLM 部分）

LLM 输出包含 `checklist`、`judgments`；不含最终 `verdict`（由程序写入 `review_report`）。

### 5.3 后处理

- `fix_decision` 但无法提取有效 `fix_instructions` → 降为 `pass`（避免死循环）。

### 5.4 内循环（逻辑）

```
for review_round in range(two_c_max_review_rounds + 1):
    运行 l2b_decision（带 review_fixes / force_pass）
    运行 l2c_review
    若 verdict == pass → 进入 l2d_human
    若未过且仍有轮次 → 继续
    否则 force_pass 后进入 l2d_human
```

（具体轮次与调度细节以 `pipeline_scheduler.py` 为准。）

### 5.5 配置

| 项 | 默认 | 含义 |
|----|------|------|
| `two_c_max_review_rounds` | `1` | `0` 关闭真实审核 |
| `two_c_must_pass_rate` | `1.0` | must 项通过率阈值 |

---

## 6. 2d 人工子阶段

### 6.1 命令与效果（TUI / Shell）

| 输入 | 效果 |
|------|------|
| `f1 <text>` | REFLOW → 2a |
| `f2 <idx> <old> <new>` | REFLOW → 2a |
| `f3 <text>` | REFLOW → 2b（可注入 `_selection_opinion`） |
| `f4 <idx,...>` | 直接改 `keep_mask` |
| `t <index>` | toggle 单句 |
| `a` / `AcceptAction` | 定稿，`l2d_completed=True` |
| `q` | 中止流水线 |

### 6.2 REFLOW 与上限

- `PipelineSession` 限制回流次数 `two_d_max_reflows`（默认 3，`0` 禁用）。
- REFLOW_2A：重置 2a 及下游；REFLOW_2B：重置 2b 及下游。

### 6.3 Auto 模式

`l2d_human` 若 **`ctx.pending_action is None`**（无交互队列），直接执行 `AcceptAction()`，等价 CLI 自动确认。

---

## 7. 与 L1 / L3 的契约

### 7.1 与 L1

- L1 单节点 `l1_perception` 产出带时间的 `annotations[]` 与 `raw_text`。
- `len(tokens) == len(annotations)`，且 `index` 对齐。
- L2 **不**改写 `annotations[].content`。

### 7.2 与 L3

- L3 读取 **`annotations[]`（含 `t_start`/`t_end`）** 与 **`current.keep_mask[]`**。
- `l3_execute` 在调用执行层前会将顶层 `keep_mask` 同步到 `current.keep_mask`（若存在）。
- 时间区间在 `execution` / `timeline_segments` 中合成；**EDL 不落盘**。
