# AutoSmartCut 智能层设计变更说明

> 本文档记录智能层（Layer 2）设计的演进过程，对比原有设计与当前确认设计的差异，说明变更理由。
> 具体设计规范和实现细节请参阅 [intelligence-design.md](intelligence-design.md)。

---

## 0. MVP 现行摘要（工程契约）

**实现与层间 JSON 以 [intelligence-layer2-mvp.md](intelligence-layer2-mvp.md) 与 [AutoSmartCut-MVP.md](AutoSmartCut-MVP.md) 为准。** 与下文历史表格冲突时，以这两份文档为准。

- **Layer1 → LLM 句面**：自清单 **`annotations[]`** 派生内存 **`tokens[]`**（仅 `index` + `text`），见 `annotation_tokens.tokens_from_annotations`；**不**再经独立 JSON2 文件入口。
- **2a**：R1（内存）`purpose_rough`、`outline_blocks_rough`、`candidate_misrecognitions`；R2（内存）`purpose`、`outline_blocks`、`corrections`；**程序**在 **`tokens[].text`** 上生成稠密 `cleaned_annotations[]`；**不**持久化 `symbol_table` / 候选表 / `corrections`。
- **2b**：`keep_mask`（与 **`tokens[]`** / **`annotations[]`** 等长）；**EDL 等价区间**在执行层由时间与 mask 合成，见 intelligence-layer2-mvp §11。

以下各节保留**演进讨论**；表格中「当前设计」若未反映上列契约，视为已被 MVP 文书覆盖。

---

## 1. 原有设计极简概述

**2a 理解层的两轮分工：**
- Round 1（bootstrap）：识别专有名词误识，输出 `symbol_table`
- Round 2（消歧+理解）：应用 `symbol_table`，生成 `cleaned_annotations` 和 `checklist`
- 符号表一次性产出，纠错完成后固化不变，重跑时不触碰

**用户反馈处理：**
- 统一通过 `[r]` 自然语言反馈触发 2a 重跑
- 所有反馈均进入 2a，不区分反馈类型

**其他层级：**
- 2b 决策层无严格输出验证策略
- 2c 审核层 MVP 自动通过，无反馈闭环
- 2d 人工层反馈处理不区分类型

**基础认知：**
- LLM 处理流程未深究"理解先行还是纠错先行"的问题

---

## 2. 主要变更点与理由

| 变更点 | 原有设计 | 当前设计 | 变更理由 |
|--------|---------|---------|---------|
| **2a Round 1 输出** | `purpose_rough` + `symbol_table[]` | `purpose_rough` + `segments_rough[]` + `symbol_table_candidates[]` | 分块和块总结是理解的核心产出，应在 Round 1 完成；符号表分为候选和确认两阶段，提升可调试性 |
| **符号表生命周期** | 一次性产出，Round 1 后固化 | 分为候选（Round 1）和确认（Round 2）两阶段 | 提升纠错精准度，避免候选误识被直接应用；Round 2 确认后才是最终版本 |
| **文本消歧方案** | 原地修改 `annotations[]` | 虚拟消歧，生成 `cleaned_annotations[]`，原文保留 | 保留原文溯源，避免 LLM 创意改写，保证音轨与文本对应关系 |
| **2a 重跑时的固化字段** | 重跑时不触碰 `symbol_table` 和 `cleaned_annotations` | 根据反馈类型决定是否重新生成 | 如果反馈涉及纠错（关键词识别错误），需要重新生成这些字段以保证一致性 |
| **用户反馈处理** | 统一通过 `[r]` 自然语言反馈 | 分为 4 个结构化输入框，不同类型反馈触发不同处理路径 | 降低 LLM 解析难度，避免语义模糊；理解反馈触发 2a 重跑，决策反馈在 2d overrides 处理 |
| **反馈输入框** | 无 | 4 个输入框：主旨偏差、关键词识别错误、内容选择意见、剪辑时间节点意见 | 引导用户按维度反馈，逼用户将模糊需求具体化，提升交互质量 |
| **2b 输出验证** | ��明确验证策略 | 明确 Schema 验证和 Retry 机制（最多 3 次） | 保证 `keep_mask` 格式正确，避免模型偶尔输出结构错误 |
| **2c 审核与人工层** | 2c auto-pass，2d 无覆盖提示 | 2c 继续 auto-pass，2d CLI 显示 `checklist_coverage[]` | 保持 MVP 简单，同时提升人工审阅效率，让用户一眼看到哪些要点被覆盖 |
| **LLM 处理流程认知** | 未深究 | 明确 LLM 先理解语义再纠错，与人类顺序相反 | 符合 Transformer 架构和注意力机制特性，指导设计分工和 Prompt 构造 |
| **分块与总结处理** | 未明确 | 分块和块总结在 Round 1 同时完成 | LLM 的 attention 机制支持全局理解，无需"回头看"，一次前向传播完成更高效 |

---

## 3. 核心设计原则的演进

### 原有原则
- Manifest 是唯一通信介质
- Append-only 保证重跑安全
- 步骤函数各自独立
- 显式状态机控制流程

### 当前原则（保持不变）
- 上述原则全部保留
- 新增：**反馈分类映射**，不同类型反馈走不同处理分支
- 新增：**虚拟消歧**，保留原文溯源
- 新增：**结构化反馈**，降低语义歧义

---

## 4. 设计演进的关键洞察

### 洞察 1：LLM 的处理顺序与人类不同
- **人类**：先纠错（整理文本）→ 再理解（读通顺的文本）
- **LLM**：先理解（全文语义）→ 再纠错（在理解基础上识别错误）
- **影响**：这改变了对 2a 两轮分工的理解，Round 1 应该完成"理解"（包括分块），Round 2 完成"确认和应用"

### 洞察 2：虚拟消歧 vs 原地修改
- **原地修改的风险**：LLM 可能创意改写，改变措辞和语序，导致文本与音轨语义不一致
- **虚拟消歧的优势**：保留原文，通过符号表映射，保证溯源性和音轨一致性

### 洞察 3：反馈分类的必要性
- **统一反馈的问题**：所有反馈都触发 2a 重跑，导致不必要的计算和复杂度
- **分类反馈的优势**：理解反馈 → 2a，决策反馈 → 2d，精准修改对应模块

### 洞察 4：结构化反馈的价值
- **自由文本反馈的问题**：LLM 需要解析自然语言，容易产生歧义和误解
- **结构化反馈的优势**：用户按维度填写，代码层直接映射，减少 LLM 解析负担

---

## 5. 与其他文档的关系

- **[AutoSmartCut.md](AutoSmartCut.md)**：架构愿景，定义整体系统设计理念
- **[AutoSmartCut-MVP.md](AutoSmartCut-MVP.md)**：MVP 落地规划，整体 MVP 范围和技术决策
- **[intelligence-design.md](intelligence-design.md)**：智能层详细设计规范，本文档的实现基准
- **[after-mvp-todo.md](after-mvp-todo.md)**：MVP 后的扩展规划，包括含释义符号表、长视频分���等

---

## 6. 后续参考

**MVP 工程实现**请以 [intelligence-layer2-mvp.md](intelligence-layer2-mvp.md) 与修订后的 [AutoSmartCut-MVP.md](AutoSmartCut-MVP.md) 为准；完整版草图与历史流程见 [intelligence-design.md](intelligence-design.md)。

本文档主要用于理解设计演进过程和变更背景。

---

*文档版本：0.1.0*  
*创建日期：2026-04-08*  
*对应设计文档：[intelligence-design.md](intelligence-design.md)*
