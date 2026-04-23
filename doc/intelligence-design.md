# 智能层设计（Layer 2 Intelligence）

> 本文档是 [AutoSmartCut-MVP.md](AutoSmartCut-MVP.md) 的配套设计文档，专门记录智能层的架构决策与实现细节。
> 架构愿景见 [AutoSmartCut.md](AutoSmartCut.md)。
> 设计演进过程和变更说明见 [intelligence-decisions.md](intelligence-decisions.md)。
>
> **说明（MVP）**：当前 MVP 的 Layer 2 实现与评审以 [intelligence-layer2-mvp.md](intelligence-layer2-mvp.md) 为准。本文保留为历史/完整版设计参考，不作为当前 MVP 的直接实现依据。  
> **补充（工程现状）**：现行代码中 L2 **从 `timeline_manifest.json` 读 `annotations[]`**，在内存中注入 **`manifest["tokens"]`**（`annotation_tokens.tokens_from_annotations`），写回 **`current`**；**不**再以独立 JSON2 文件为入口。  
> **冲突处理**：凡本文与 intelligence-layer2-mvp 不一致之处（例如状态机中的 **`symbol_table` 固化**、**2d 后编译 `edl[]`**、**`segments` 嵌套 `keep_mask`**、2a 直接 LLM 输出整表 `cleaned_annotations` 等），**均以 intelligence-layer2-mvp 与 [AutoSmartCut-MVP.md](AutoSmartCut-MVP.md) 修订版为准**；下文状态机与 §4 起可视为**完整版草图**。

---

## 目录

1. [设计原则](#1-设计原则)
2. [整体结构](#2-整体结构)
3. [周目与跨周目管理](#3-周目与跨周目管理)
4. [2a 理解层](#4-2a-理解层)
5. [2b 决策层](#5-2b-决策层)
6. [2c 审核层](#6-2c-审核层)
7. [2d 人工层](#7-2d-人工层)
8. [反馈分类与层级映射](#8-反馈分类与层级映射)
9. [Token 消耗监控](#9-token-消耗监控)
10. [MVP 简化对照](#10-mvp-简化对照)

---

## 1. 设计原则

**P1：manifest 是唯一通信介质**

步骤函数之间不直接传参。每个步骤从 manifest 读自己需要的字段，写回自己产出的字段。调度器不手动串联中间变量，manifest 的当前状态即是完整上下文。

**P2：步骤函数各自独立，不共享基类**

四个子阶段（2a/2b/2c/2d）共享同一外层契约 `f(manifest_state) → manifest_mutations`，但内部逻辑差异大：prompt 构造完全不同，2d 根本不调 LLM。共享的机械动作（HTTP 调用、JSON 解析、重试、schema 校验）集中在 **`autosmartcut.intelligence_llm`**：`call_llm_structured` / `call_turn_structured` 等，不用基类。

**P3：状态机控制流程，显式优于表驱动**

控制流转（2a→2b→2c→2d 及各种回溯路径）通过 ~40 行显式 `if/elif` 状态机实现，任何人看一眼即知流程走向。状态转移只在四个外部边界上发生，不在 2a 的内部轮次间发生。

**P4：Append-only 保证重跑安全**

**完整版叙事**：重跑时，固化字段（`symbol_table`、`cleaned_annotations`）不被覆盖，只有可变字段（`purpose`、`checklist`）被更新。  
**MVP**：不持久化 `symbol_table`；`cleaned_annotations` 由程序根据 R2 `corrections` 生成；仍不原地改写 Layer1 `annotations[].content`。Append-only 指清单上已写入的上游字段不被下游抹掉。

---

## 2. 整体结构

### 2.1 三个构件

```
intelligence.py
├── llm_call()           ← 共享传输层
├── step_2a()            ← 理解层（含冷启动/重跑两种内部模式）
├── step_2b()            ← 决策层
├── step_2c()            ← 审核层（MVP: auto-pass）
├── step_2d()            ← 人工层（CLI 交互，不调 LLM）
└── run_intelligence()   ← 状态机主循环
```

**`llm_call()`** — 共享传输层

处理所有与语义无关的机械动作：

```python
@dataclass
class LLMResponse:
    parsed: dict        # 解析后的 JSON
    tokens_in: int
    tokens_out: int
    raw_content: str    # 原始响应，用于审计

def llm_call(
    client: OpenAI,
    model: str,
    messages: list[dict],
    *,
    max_retries: int = 3,
    temperature: float = 0.3,
) -> LLMResponse:
    ...
```

**`StepOutput`** — 步骤函数的统一返回类型

```python
@dataclass
class StepOutput:
    mutations: dict        # 要写回 manifest 的字段
    tokens_used: int
    signal: str | None     # 控制信号，仅 2c/2d 产出
                           # "2a" | "2b" | "2d" | "done"
```

### 2.2 完整状态机流转图

```mermaid
flowchart TD
    entry([入口]) --> guard_init[初始化守卫\nouter=0 inner=0 tokens=0]
    guard_init --> stage_2a

    subgraph stage2a [2a 理解层]
        stage_2a{cleaned_annotations\n已存在?}
        cold[冷启动模式\nR1+R2 共2次调用]
        rerun[重跑模式\n1次调用]
        stage_2a -- 否 --> cold
        stage_2a -- 是 --> rerun
    end

    cold --> write_2a[写入: purpose + checklist\ncleaned_annotations + symbol_table]
    rerun --> write_2a_r[写入: purpose + checklist\n不动 symbol_table/cleaned_annotations]
    write_2a --> stage_2b
    write_2a_r --> stage_2b

    subgraph stage2b [2b 决策层]
        do_2b[1次 LLM 调用]
    end
    stage_2b --> do_2b
    do_2b --> write_2b[写入: keep_mask\nchecklist_coverage]
    write_2b --> guard[终止守卫]

    guard --> stage_2c

    subgraph guard_box [终止守卫 代码层判断]
        guard{"token_spent >= budget\nOR rounds >= max\nOR coverage >= 0.9"}
    end

    guard -- 强制pass --> stage_2c

    subgraph stage2c [2c 审核层]
        do_2c[完整版: LLM审核\nMVP: auto-pass]
    end
    stage_2c --> do_2c
    do_2c --> verdict{verdict}

    verdict -- fix_checklist --> inc_outer[outer_rounds++]
    inc_outer --> stage_2a

    verdict -- fix_decision --> inc_inner[inner_rounds++]
    inc_inner --> stage_2b

    verdict -- pass --> stage_2d

    subgraph stage2d [2d 人工层]
        human_op{操作}
    end
    stage_2d --> human_op

    human_op -- "[a] 确认" --> merge[merge keep_mask\n+ 所有 overrides]
    merge --> edl[编译 edl[]]
    edl --> done([layer_completed=2\n流转 Layer 3])

    human_op -- "[t] 切换" --> overrides[追加 overrides delta\n刷新时长预览]
    overrides --> stage_2d

    human_op -- "[r] 反馈" --> feedback[追加 feedback_text\nsignal = '2a']
    feedback --> stage_2a
```

### 2.3 MVP 退化形态

MVP 中循环禁用（`max_inner=0, max_outer=0`），2c auto-pass，状态机退化为线性序列：

```mermaid
flowchart LR
    entry([入口]) --> A
    A["2a 冷启动\nR1+R2"] --> B
    B["2b 决策\n1次调用"] --> C
    C["2c auto-pass\n写入 verdict=pass"] --> D
    D["2d 人工审阅\nCLI"]

    D -- "[a] 确认" --> edl([edl[] 写入\n流转 Layer 3])
    D -- "[t] 切换" --> D
    D -- "[r] 反馈" --> A2["2a 重跑\n1次调用\n不重新消歧"]
    A2 --> B
```

---

## 3. 周目与跨周目管理

### 3.1 周目的定义

**一个周目 = 2a→2b→2c→2d 的完整循环。**

每个周目是独立的处理单元，有独立的 TimelineManifest 实例。跨周目时创建新的 Manifest，将前一周目作为参考上下文，而非覆盖原数据。

**关键原则：**
- 新周目继承前一周目的反馈信息，但不修改前一周目的数据
- 符合 Append-only 原则，保证数据可追溯性和重跑安全性
- 跨周目反馈需要结构化，明确指向"理解问题"还是"决策问题"

### 3.2 跨周目的反馈流转

用户的反馈在 2d 人工层产生，根据反馈类型映射到不同的处理路径：

| 反馈类型 | 来源 | 处理方式 | 是否创建新周目 |
|---------|------|---------|--------------|
| 理解反馈 | 输入框 1、2、3（主旨、关键词、内容选择） | 创建新周目，重跑 2a | **是** |
| 决策反馈 | 输入框 4（剪辑时间节点） | 在当前周目的 2d overrides 中处理 | **否** |

**理解反馈的处理：**
- 创建新���目，进入 2a 重跑模式
- 根据反馈内容决定是否重新生成 `symbol_table` 和 `cleaned_annotations`
  - 如果反馈涉及"纠错"（关键词识别错误），需要重新生成
  - 如果反馈仅涉及"理解"（主旨、内容选择），继承前一周目的消歧结果

---

## 4. 2a 理解层

**MVP 现行**（R1/R2/程序替换、`tokens`、持久化字段）见 [intelligence-layer2-mvp.md §5](intelligence-layer2-mvp.md)；**本节下文为完整版/历史契约草图**。

### 4.1 对外契约

**输入：**
- `manifest.annotations[]`（Layer 1 产出的原始标注，含 ASR 误识）
- `manifest.goal`（用户指定的分析目标）

**输出（写入 `manifest.comprehension`）：**
- `purpose`：视频内容的核心目标与关键信息点
- `checklist[]`：结构化内容要点，含 must/optional 优先级
- `cleaned_annotations[]`：消歧后的标注文本（原始标注保持不变，Append-only）

**审计字段（不向下游传递）：**
- `symbol_table[]`：专有名词的误识形式与正确形式对照表，持久化供审计

> **关键约束：** 2b/2c 的输入不包含 `symbol_table`。符号表的知识在 2a Round 2 中已溶解进 `cleaned_annotations`，下游消费的是消歧后的文本，不需要知道原始映射。

### 3.2 comprehension 字段稳定性分类

| 字段 | 稳定性 | 来源 | 重跑行为 |
|------|--------|------|---------|
| `symbol_table` | **固化**（Round 0 后不变）| 从不可变的 ASR 原文推导 | 不重算 |
| `cleaned_annotations` | **固化**（Round 0 后不变）| 从 ASR 原文 + symbol_table 推导 | 不重算 |
| `purpose` | **可变** | 对内容的解读角度，受反馈/约束影响 | 每轮可更新 |
| `checklist` | **可变** | 同上 | 每轮可更新 |

稳定性的根本依据：`symbol_table` 和 `cleaned_annotations` 的输入是不可变的 ASR 原文，只要 ASR 输出不变它们就不会变。重跑消歧的边际收益为零，且随机温度可能引入噪声。

### 3.3 冷启动模式（Round 0）

**触发条件：** `manifest.comprehension` 为空或 `cleaned_annotations` 为空。

**执行：两次 LLM 调用**

```
Round 1（bootstrap）
  输入: annotations 原始文本 + goal
  输出: { purpose_rough, symbol_table[] }
  目的: 先聚焦识别专有名词误识，认知负荷单一

Round 2（消歧+理解）
  输入: annotations 原始文本 + goal
        + purpose_rough（Round 1 产出）
        + symbol_table[]（Round 1 产出）
  输出: { purpose, checklist[], cleaned_annotations[] }
  目的: 拿着已有结论做查表确认式消歧，同时精化理解
```

**两轮分离的理由：** 若一轮完成，LLM 必须同时做"发现系统性误识"和"根据误识做消歧"这两件事，认知负荷过大，一致性难以保证。两轮分工明确，Round 1 识别，Round 2 应用，每轮任务单一。

### 3.4 重跑模式（Round 1+）

**触发条件：** `cleaned_annotations` 已存在（重跑 2a 时）。

**执行：一次 LLM 调用**

```
输入:
  cleaned_annotations[]   ← 已有，不重新生成
  goal                    ← 不变
  上一轮 purpose          ← 作为增量修正的起点
  上一轮 checklist[]      ← 作为增量修正的起点
  触发原因（二选一）:
    completeness_issues[] ← 2c 发现检查清单遗漏维度（外循环）
    feedback_text         ← 用户自然语言反馈（2d [r] 操作）

输出:
  { purpose（更新）, checklist[]（更新）}
  不产出 symbol_table / cleaned_annotations（不触碰固化字段）
```

**增量修正的 Prompt 策略：** 将上一轮的 `purpose` 和 `checklist` 显式传入，告知 LLM 在已有理解的基础上按触发原因调整，而不是从零重建。

### 3.5 符号表（Symbol Table）的定位

**为什么需要：**

ASR 误识具有**系统性**：同一个专有名词在同一视频中往往被一致误识成同一个错误形式（相同说话人、相同声学环境、模型对特定音节的固定偏好）。符号表把 Round 2 的任务从"开放式识别哪里有错"降维为"按表批量替换+语境确认"，保证纠正一致性。

**收录范围：**

| 类别 | 收录标准 |
|------|---------|
| 人名 | 出现 ≥2 次，且姓名组合不常见 |
| 专有术语 | 领域词汇，ASR 容易拆解为日常词组 |
| 产品/项目名 | 中英文混合场景误识高发区 |
| 机构/地名 | 低频专有名词 |

不收录：单次随机口误、日常词汇偶发错误、句法层面的错误（符号表只解决字级误识）。

MVP 预期条目数：**5–20 条**（取决于视频专业程度）。

**生命周期：**

```
ASR 原文（含误识）
    │
    │ [2a Round 1]
    ├──────────────→ symbol_table     ← 在此建立，R1 的工作台
    │
    │ [2a Round 2]
    ├──────────────→ cleaned_annotations  ← 知识溶解于此
    │               （symbol_table 的使命完成）
    │
    │ [持久化]
    └──────────────→ comprehension.symbol_table  ← 审计用，非下游输入
```

**关于 RAG：** MVP 及长视频场景均不需要 RAG。5–10 分钟视频的总文本量约 3000–6000 字，远在 DeepSeek 128K 上下文窗口内。长视频场景的正确方案是**符号表作全局头部注入 + 滑窗分块**：每个分块的 Prompt 以 `[全局头: purpose + checklist + symbol_table]` 开头，后接本块的 annotations 切片。符号表约 600 字，相比 RAG 管线简单且无信息损失。

---

## 4. 2b 决策层

**输入（从 manifest 读取）：**
- `comprehension.purpose`
- `comprehension.checklist[]`
- `comprehension.cleaned_annotations[]`（消歧后文本）
- `annotations[]`（句级语音；含 `gap_after`，无独立静音行）

> 注意：`comprehension.symbol_table` **不在输入列表中**。

**输出（写入 manifest）：**
- `segments[].keep_mask[]`：每条 annotation 的保留决策
- `segments[].checklist_coverage[]`：checklist 各项的覆盖情况

**keep_mask 格式约束：**
- `len(keep_mask) == len(annotations)`（与清单句级条数一致），不得有缺失 index
- 每条：`keep=true/false`（LLM 决策）

---

## 5. 2c 审核层

**现行实现（已上线）：**

2c 是 2b 决策的结构化验证器。在单次 LLM 调用中，先将模糊的 goal 分解为一组可判真假的 checklist 条目（基于 goal + purpose + outline_blocks），再逐条对照 keep_mask 做布尔判断。verdict 由程序根据 must 项通过率计算，不由 LLM 直接输出。

- 输入：`goal + purpose + outline_blocks + cleaned_annotations + keep_mask`
- LLM 两阶段输出：`checklist[]`（审核条件）→ `judgments[]`（逐条判断 + evidence_indices）
- 程序计算 verdict：`pass`（must 项全部通过）或 `fix_decision`（有 must 项未通过）
- `fix_decision` 时提取 `fix_instructions`（含具体应保留的 index），注入 2b prompt 重跑

| verdict | 含义 | 后续动作 |
|---------|------|---------|
| `pass` | must 项全覆盖 | 流转 2d |
| `fix_decision` | must 项未覆盖，携带具体 index | 回到 2b（内循环），注入 `fix_instructions` |

**配置**：`two_c_max_review_rounds`（默认 1，`0`=占位透传）、`two_c_must_pass_rate`（默认 1.0）。

**完整版扩展方向**：
- `fix_checklist` → 回到 2a（外循环），携带 `completeness_issues[]`（当前未实现）。
- Token 预算守卫控制循环上限。

---

## 6. 2d 人工层

2d 是整条管道唯一的人工介入点，通过 CLI 交互实现，不调用 LLM。

| 操作 | 行为 | LLM 调用 |
|------|------|---------|
| `[a]` 确认通过 | merge(keep_mask + 所有历史 overrides) → 编译 `edl[]` | 否 |
| `[t N]` 切换 keep/cut | 追加 `overrides` delta，刷新时长预览，等待下一步操作 | 否 |
| `[r]` 自然语言反馈 | 追加 `feedback_text` 到 `human_feedback_history[]`，触发 2a 重跑 | 是（重跑 2a）|

**overrides 的 delta 模式：**

`[t]` 操作不修改 `segments[].keep_mask` 原地值，而是向当前 `HumanFeedbackRound.overrides[]` 追加 delta 记录 `{"index": int, "keep": bool}`。最终有效决策：

```
effective_keep[i] = 最后一条覆盖 index i 的 override（若有）
                    否则 keep_mask[i]
```

`[a]` 确认时一次性合并所有 overrides 编译为 `edl[]`，这是 `edl[]` 的唯一写入点。

**`[r]` 触发重跑的行为：**
- 追加一条 `HumanFeedbackRound(verdict="feedback", feedback=<用户输入>)`
- 状态机 signal 返回 `"2a"`，进入 2a **重跑模式**（不冷启动，不重新消歧）
- 2a 重跑将 `feedback_text` 作为触发原因注入 Prompt

---

## 7. MVP 简化对照

| 子阶段 | 完整版 | MVP 实现 |
|--------|--------|---------|
| 2a 冷启动 | 2次 LLM 调用（R1 bootstrap + R2 消歧+理解）| **相同，不简化** |
| 2a 重跑 | 外循环（2c fix_checklist 驱动）+ 人工反馈 | **仅由 [r] 人工反馈触发**；`fix_checklist` 路径不存在 |
| 2b 决策 | 1次 LLM 调用 | **相同**；修正重跑时注入 2c 的 `review_fixes` |
| 2c 审核 | LLM 逐项核查，三种裁决 + 内/外循环 | **已实现 LLM 审核**（`two_c_max_review_rounds >= 1`）；单次调用两阶段输出（checklist → judgments）；verdict 由程序计算；仅 `pass`/`fix_decision` 两种裁决（无 `fix_checklist`）；`= 0` 时退化为占位透传 |
| 内循环 | `fix_decision → 2b`，上限 `max_inner` | **已实现**：`two_c_max_review_rounds`（默认 1）控制 2b↔2c 循环上限 |
| 外循环 | `fix_checklist → 2a`，上限 `max_outer` | **禁用**（无 `fix_checklist` 路径） |
| Token 预算 | 按视频时长动态分配 | **固定调用次数**：2a×2 + 2b×1 + 2c×1 = 4 次（一次通过）；修正 1 轮最多 6 次 |
| 2d 人工层 | CLI 交互 | **相同** |

MVP 中每次运行的 LLM 调用次数：首轮 4 次（2a×2 + 2b×1 + 2c×1）；2c 修正每轮额外 2 次（2b + 2c）；每次 `[r]` 反馈额外 1 次（重跑 2a 重跑模式）。

---

*文档版本：0.1.0*
*对应代码：`autosmartcut/stages/intelligence.py`（待实现）*
*关联文档：[AutoSmartCut.md](AutoSmartCut.md) · [AutoSmartCut-MVP.md](AutoSmartCut-MVP.md)*
