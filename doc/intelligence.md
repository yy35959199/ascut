# 智能层设计（Layer 2）

> 最后更新：2026-04-24

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

智能层包含四个子阶段，按以下顺序执行：

```
2a 理解 → 2b 决策 ↔ 2c 审核（内循环）→ 2d 人工
                                          │
                              REFLOW_2A ──┤（F1/F2 反馈）
                              REFLOW_2B ──┘（F3 反馈）
```

**LLM 调用次数汇总：**

| 场景 | 调用次数 | 说明 |
|------|---------|------|
| 2c 关闭（`two_c_max_review_rounds=0`） | 3 次 | 2a×2 + 2b×1 |
| 2c 开启，一次通过 | 4 次 | 2a×2 + 2b×1 + 2c×1 |
| 2c 开启，修正 1 轮 | 6 次 | 2a×2 + 2b×1 + 2c×1 + 2b×1 + 2c×1 |
| 每次 REFLOW_2A（F1/F2） | +3 或 +4 次 | 重跑 2a（1次）+ 2b + 2c |
| 每次 REFLOW_2B（F3） | +2 或 +3 次 | 重跑 2b + 2c |

---

## 2. 数据契约

### 各子阶段输入/输出字段

| 子阶段 | 从 manifest 读取 | 写入 manifest |
|--------|----------------|--------------|
| **2a** | `annotations_l1a`（→ 派生 `tokens[]`）、`goal` | `comprehension`（含 `purpose`、`outline_blocks[]`、`corrections[]`） |
| **2b** | `comprehension`、`annotations_l1a`（→ 派生 `tokens[]`、`cleaned_annotations[]`） | `keep_mask[]` |
| **2c** | `keep_mask`、`comprehension`、`annotations_l1a`、`goal` | `review_report` |
| **2d** | `keep_mask`、`review_report`、`comprehension` | `human_feedback_history[]`、`l2d_completed`（标识位） |

**运行时派生（不落盘）：**
- `tokens[]`：由 `annotation_tokens.tokens_from_annotations(annotations_l1a)` 生成，每条仅含 `index` 和 `text`
- `cleaned_annotations[]`：由程序根据 `comprehension.corrections` 在 `tokens[].text` 上替换生成，稠密全量

保存 manifest 前，`manifest_io.strip_volatile_fields()` 会剥离 `tokens`、`cleaned_annotations` 等运行时键。

---

## 3. 2a 理解子阶段

### 3.1 执行流程

```
R1（LLM）→ R2（LLM，与 R1 同一对话前缀）→ 程序步骤（生成 cleaned_annotations）
```

**R1 输出（中间态，仅内存）：**
- `purpose_rough`：粗糙主旨
- `outline_blocks_rough[]`：草稿分块（`start_index`/`end_index`/`topic`）
- `candidate_misrecognitions[]`：疑似 ASR 误识（`annotation_index`、`wrong`、`suggestions[]`）

**R2 输出（中间态，仅内存）：**
- `purpose`：精化主旨
- `outline_blocks[]`：最终分块（`start_index`/`end_index`/`summary`）
- `corrections[]`：唯一替换列表，每条含 `index`（句子 index）、`old`（原文错误子串）、`nth`（第几次出现，1-based）、`new`（替换词）

**程序步骤：**
根据 `corrections` 在 `tokens[].text` 的只读副本上做替换，生成稠密全量 `cleaned_annotations[]`（每条含 `annotation_index` 和 `cleaned_content`）。未发生纠错的句子，`cleaned_content` 与 `tokens[].text` 相同。不改写 `annotations[].content`，不修改已载入的 `tokens[]` 原文。

**持久化字段（写入 `manifest.comprehension`）：**
- `purpose`
- `outline_blocks[]`（字段名为 `summary`）
- `corrections[]`（供程序重算 `cleaned_annotations`）

### 3.2 真多轮 LLM API

R1 和 R2 使用同一对话前缀（真多轮），利于前缀缓存命中：

| 能力 | 入口 | 说明 |
|------|------|------|
| R1 单轮 + 原文快照 | `call_once_structured_with_raw_content` | 返回 `data`、`assistant_content`（R1 JSON 字符串）、`request_messages`（供 R2 前缀） |
| 拼接 R1→R2 | `prepare_next_turn_messages` | 在 `request_messages` 后追加 `assistant`（R1 内容）与 R2 的 `user` 消息 |
| R2 多轮下一跳 | `call_turn_structured` | 对 messages 先 `sanitize_messages_for_api`（去掉 `reasoning_content`），再请求 |

跨轮不得把历史轮的 `reasoning_content` 拼进 `messages`（由 `sanitize_messages_for_api` 保证）。

---

## 4. 2b 决策子阶段

### 4.1 输入

- `comprehension.purpose`
- `comprehension.outline_blocks[]`
- `comprehension.cleaned_annotations[]`（稠密全量，运行时派生）
- `tokens[]`（运行时派生，用于构造 Prompt）

时间字段（`t_start`/`t_end`/`gap_after`）不进入 LLM Prompt，留在 `annotations[]` 供 L3 消费。

### 4.2 输出格式

`keep_mask[]` 约束：
- 长度必须等于 `len(tokens)`（与 `annotations[]` 条数相同）
- 每条：`{"index": int, "keep": bool}`，`keep` 仅为布尔值，不使用 `null`
- 按 `index` 升序，不得有缺失或重复 index

### 4.3 single 模式 vs block 模式

由 `config.toml` 的 `[intelligence] two_b_mode` 控制（CLI `--two-b-mode` 可覆盖）：

| 模式 | 行为 |
|------|------|
| `single`（默认） | 全文一次 LLM 调用，输出完整 `keep_mask[]` |
| `block` | 按 `outline_blocks[]` 迭代，每块一次 LLM 调用，合并结果为完整 `keep_mask[]` |

### 4.4 校验与重试

- 校验项：`keep_mask` 长度、index 对齐、类型约束
- 失败时整体重试，最多 3 次
- 3 次均失败则节点返回 FAILED，流水线中止

### 4.5 修正重跑时的 review_fixes 注入

2c 返回 `fix_decision` 时，FixedScheduler 重新调度 l2b_decision 并注入参数：
- `review_round`：当前修正轮次（0-based）
- `two_b_mode`：当前模式
- `review_fixes`：从 `review_report.fix_instructions` 提取的修正指令

修正指令注入 2b Prompt 的「阶段定位」之后，要求 LLM 优先修正指定 index 的 keep 决策。

---

## 5. 2c 审核子阶段

### 5.1 设计原理

2c 是 2b 决策的结构化验证器，用不同认知视角检验 2b 的输出：2b 是正向逐句决策（「这句该不该留」），2c 是逆向验证（「删掉这些之后，剩下的是否满足用户目标」）。

**为什么需要 checklist**：LLM 对模糊目标做 pass/not-pass 的二值判断不稳定。将 goal 分解为离散布尔条件，每条判断空间远小于原始 goal 的判断空间。

**为什么在同一次调用内生成 checklist 并逐条判断**：两阶段是因果推理链——先生成的 checklist token 直接参与后续判断的注意力计算。拆成两次调用会引入两层独立采样的随机性叠加。

**为什么 verdict 由程序计算**：LLM 生成 verdict 时可能与自己的 judgments 矛盾。程序计算是确定性的，阈值可配置，不需要改 prompt 就能调整审核严格度。

### 5.2 Prompt 结构（五个区段）

1. **阶段定位**：声明当前为 2c 审核层，任务是验证而非重新决策
2. **上下文注入**：goal + purpose + outline_blocks 摘要
3. **审核材料**：按 outline_block 分组展示句面，每句标注 `[✓]`（保留）或 `[✗]`（删除）
4. **第一步任务指令（生成 checklist）**：强制声明「此步必须优先完成，在 checklist 数组完整输出之前，禁止开始第二步」
5. **第二步任务指令（逐条判断）**：强制声明「禁止依赖记忆或整体印象，必须从头重新逐句阅读原文」，每条判断必须给出 `evidence_indices`

### 5.3 输出 Schema

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

`verdict` 不在 schema 里，由程序计算。

### 5.4 程序层后处理

1. **verdict 计算**：统计 must 项通过率，与 `two_c_must_pass_rate` 比较
   - 通过率 >= 阈值 → `verdict = "pass"`
   - 通过率 < 阈值 → `verdict = "fix_decision"`
2. **fix_instructions 提取**：从未通过的 must 项中提取 `{requirement, missing_indices, note}`，仅提取 `evidence_indices` 非空的项
3. **边界情况**：若 `fix_instructions` 为空但 verdict 为 `fix_decision`，强制改为 `pass`（无法给出有效修正指令时，交给 2d 人工兜底）

### 5.5 2b↔2c 内循环控制流

```
for review_round in range(two_c_max_review_rounds + 1):
    调度 l2b_decision（首次无 fixes，修正重跑时注入 review_fixes）
    调度 l2c_review
    if verdict == "pass": 调度 l2d_human; break
    if review_round < two_c_max_review_rounds:
        提取 fix_instructions → 下一轮 l2b 注入
    else:
        注入 force_pass=True → l2b 强制通过 → 调度 l2d_human
```

### 5.6 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `two_c_max_review_rounds` | `1` | 最大修正轮次。`0`=占位透传（不调 LLM），`1`=审核+最多 1 轮修正 |
| `two_c_must_pass_rate` | `1.0` | must 项通过率阈值。`1.0`=全部 must 必须通过才 pass |

---

## 6. 2d 人工子阶段

### 6.1 四种反馈类型

| 命令 | 反馈类型 | 触发效果 |
|------|---------|---------|
| `f1 <text>` | F1 主旨偏差（`F1_PURPOSE_DRIFT`） | REFLOW → `l2a_comprehension`（重跑 2a） |
| `f2 <idx> <old> <new>` | F2 关键词纠错（`F2_KEYWORD_ERROR`） | REFLOW → `l2a_comprehension`（重跑 2a） |
| `f3 <text>` | F3 内容选择意见（`F3_SELECTION_OPINION`） | REFLOW → `l2b_decision`（重跑 2b，注入 `_selection_opinion`） |
| `f4 <idx,idx,...>` | F4 批量切换时间节点（`F4_TIME_POINT`） | 直接修改 `keep_mask`，不触发 REFLOW |
| `t <index>` | 切换单句 keep/cut | 直接修改 `keep_mask`，不触发 REFLOW |
| `a` | 确认（`AcceptAction`） | 合并 overrides，写入 `l2d_completed`，返回 SUCCESS |
| `q` | 退出（`QuitAction`） | 返回 FAILED，流水线中止 |

### 6.2 REFLOW 协议

L2dNode 返回 `StageResult(status=REFLOW, reflow_target=...)` 时，PipelineSession 的 `_handle_reflow()` 执行：

1. 检查 `_reflow_count < _max_reflows`（由 `two_d_max_reflows` 配置，默认 3）
2. 保存回流前检查点（原子写 manifest）
3. 递增 `_reflow_count`，重置 `_review_round`
4. 重置目标节点及其所有下游节点状态为 `pending`
5. 清除 manifest 中被重置节点的 `writes` 字段
6. 清除 `layer_status` 中对应的完成标记

**REFLOW_2A**（`reflow_target="l2a_comprehension"`）：重置 l2a、l2b、l2c、l2d 四个节点

**REFLOW_2B**（`reflow_target="l2b_decision"`）：重置 l2b、l2c、l2d 三个节点

达到 `two_d_max_reflows` 上限后，忽略回流请求，继续等待用户确认或退出。

### 6.3 AcceptAction 处理

`intelligence_2d_core.run_2d(manifest, AcceptAction())` 执行：
- 将所有 overrides 合并到 `keep_mask`（`effective_keep = merge(keep_mask, overrides)`）
- 将合并后的 `keep_mask` 写回 manifest（覆盖写，作为定稿）
- 写入 `human_feedback_history[]` 记录
- L2dNode 写入 `l2d_completed = True` 标识位

### 6.4 Auto 模式

当 `ctx.pending_action is None`（CLIAdapter 不注入交互队列）时，L2dNode 自动执行 `AcceptAction()`，跳过人工审阅，直接定稿当前 `keep_mask`。

---

## 7. 与 L1 / L3 的契约

### 与 L1 的契约

- L1A 写入 `annotations_l1a`（无时间轴，仅 `index`/`content`），L2 立即可消费
- L1B 写入 `annotations`（回填 `t_start`/`t_end`/`gap_after`），L2 不依赖此字段
- L2 的 `index` 主坐标与 L1 的 `annotations[].index` 严格对齐：`len(tokens) == len(annotations_l1a)`，且逐条 index 一致
- L2 不修改 `annotations[].content`（Append-only 原则）

### 与 L3 的契约

- L2 对外的最终产物是定稿 `current.keep_mask[]`（与 `annotations[]` 等长，按 index 对齐）
- L3 读取 `annotations[]`（时间轴）+ `current.keep_mask[]`（决策），在执行层内部合成保留时间区间
- EDL 不在 L2 落盘，由 L3 内部合成后驱动 smartcut
- L2 的语义决策主坐标为 `index`，时间换算属于 L3 职责
