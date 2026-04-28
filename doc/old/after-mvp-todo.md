# After MVP TODO

> 本文档仅记录“已确认不在 MVP 内”的后续能力清单。  
> MVP 当前实现以 `doc/intelligence-layer2-mvp.md` 与 `doc/AutoSmartCut-MVP-Mini.md`（单清单）为准。

---

## Layer 2 / 智能层

- 多周目能力：支持第 N 周目、跨周目状态管理与恢复。
- 多周目数据形态：定义新周目 Manifest 与检查点文件（如 `manifest.layer2.rN`）的落盘关系。
- 跨周目上下文策略：是否将上一周目 2a 产物注入下一周目 2a，注入范围与优先级规则。
- 2d 闭环反馈：恢复结构化反馈框 1/2/3（主旨偏差、关键词纠错、内容选择）并打通回流链路。
- 2d 框 4（剪辑时间节点/气口）：确认最终归属层（执行层或新增阶段）及对应数据契约。
- checklist 机制重启：重新定义 checklist 在 2a/2b/2c 的生成、消费与验证关系。
- `checklist_coverage` 展示：在 checklist 回归后补齐 2d 审阅界面显示。
- 真实 2c 审核：启用 LLM 审核与 `pass/fix_decision/fix_checklist` 三类裁决。
- 2c 回路控制：引入内外循环边（2c -> 2b、2c -> 2a）与对应状态机实现。
- Token 与成本可观测性：记录 usage、日志与 CLI 展示。
- Token 预算守卫：实现硬预算截断，并与循环终止条件联动。
- Layer 2 异常回退策略：定义 2c/循环启用后的失败回退与人工接管规则。

---

## Layer 2 / 2a：`outline_blocks` 校验与规范化（After MVP）

> **MVP 现状**：2a 的 R1/R2 提示词与 JSON Schema **未**要求 `outline_blocks_rough` / `outline_blocks` 在几何上「全覆盖、无重叠」；R1 schema 中 `outline_blocks_rough` 甚至非必填。  
> **2b 分块调用（After MVP）**：设计与实现时 **按「`comprehension.outline_blocks` 已规范化」考量**——即清单中的分块已是闭区间、覆盖 `0..n-1`、不重叠、有序。下列工作推迟到 MVP 之后落地。

### 1. R1 结束后：程序校验（软引导 → R2）

**输入**：`outline_blocks_rough`，`n = len(tokens)`（与清单 `len(annotations)` 一致；与 Layer1 连续 `index` 约定一致）。

**检查项（建议分层）**：

- **单块合法性**：`start_index`、`end_index` 为整数；`start_index <= end_index`；区间与 `[0, n-1]` 的关系（越界块策略：丢弃 / 裁剪 / 判失败，产品定）。
- **块间关系**（在单块合法、或裁剪后）：按 `start_index` 排序；检测闭区间 **重叠**；扫描 **缺口**（是否存在未被任何块覆盖的 index）。
- **空列表**：`outline_blocks_rough` 缺失或 `[]` 可定义为允许（R2 仅从全文自行分块），或要求失败——与产品约定一致即可。

**输出**：

- `status: ok | warn`（或等价枚举）；
- 结构化 `issues[]`（未覆盖 index 列表、重叠对、越界描述等）。

**使用**：当 `issues` 非空时，将 **固定模板 + 机器生成的诊断** 追加到 **R2 用户消息尾部**，要求 R2 输出的 `outline_blocks` 必须满足全覆盖、无重叠（R2 常规指令中也应写明该不变量；尾部为「本次 rough 异常，请重点核对」）。

**说明**：R1 提示词 **不能保证**「废话也会单独成块、因而无缺口」——无程序校验则几何错误仍可能出现。

### 2. R2 结束后、写入 `comprehension` 前：规范化（硬标准形）

**位置**：与 `_densify_cleaned_annotations` 同级——**2a 流水线末尾**，对 **R2 返回的 `outline_blocks`** 做程序处理，再写入 `manifest_dict["comprehension"]["outline_blocks"]`。

**目标**：将 LLM 输出变为 **可机械迭代** 的标准分区：

- 闭区间 `[start_index, end_index]`，与现有 prompt 表述一致；
- 每个 `index ∈ [0, n-1]` **恰好属于一块**；
- 块之间 **不重叠**，按 `start_index` **升序**。

**是否算「修复」**：是——在 **明确、可测** 的策略下允许裁剪越界、合并重叠、填补缺口；若无法安全自动修复则 `**raise`**，禁止静默写入脏数据。

**建议方法拆分（逻辑职责；实现可合并为一个对外入口 + 私有步骤）**：


| 步骤        | 功能                                                                |
| --------- | ----------------------------------------------------------------- |
| 单块检查 / 裁剪 | 丢弃 `start > end`；将部分越界块 **裁剪**到 `[0, n-1]`                        |
| 排序        | 按 `start_index` 升序                                                |
| 去重叠       | 按既定策略合并或切分闭区间重叠                                                   |
| 填缺口       | 策略待定：扩展邻块 / 插入「未分类」块 / 失败；须写死规则                                   |
| 最终断言      | 覆盖 `0..n-1` 且无重叠，否则抛错                                             |
| **对外入口**  | 例如 `normalize_outline_blocks(blocks, n, *, policy) -> list[dict]` |


**放置原则**：**不要**把唯一规范化放在 2b——否则落盘清单与运行时行为分裂。2b 仅可 **断言** 或调用同一套校验（幂等）。

### 3. 与 2b 分块调用的关系（After MVP 设计假设）

- **分块 LLM 调用**（按 `outline_blocks` 迭代）依赖 **稳定分区**；故 **2b 实现按「`outline_blocks` 已由 2a 末尾规范化」假设编写**。
- **R1 校验** 不替代规范化：前者只影响 R2 提示质量；后者决定 **落盘** 几何契约。

### 4. 待办勾选（实现时拆 PR）

- 实现 R1 后 `outline_blocks_rough` 程序校验与 R2 prompt 尾部注入。
- 实现 R2 后 `outline_blocks` 规范化（策略：重叠、缺口、空列表降级）。
- 2b：按块调用 LLM + 合并 `keep_mask`；入口可断言分区合法或幂等规范化。
- 单测：越界、重叠、缺口、空列表、单块全文等用例。

---

## Layer 2 / 2b：分块决策、合并重试与运行日志（After MVP）

> 前提：`**comprehension.outline_blocks` 已由 2a 末尾规范化**（见上一节）。2b **不负责**修块；可对分区做断言，失败则快速失败。  
> **单块超大**：先与「整段一块」等价跑通，再按成本/质量调优（不阻塞首版）。

### 1. 控制流（无第二套分支）

- **统一路径**：仅保留「按 `outline_blocks` 迭代，每块一次结构化 LLM 调用，再合并 `decisions` → `keep_mask`」。
- `**outline_blocks` 为空**：**不**单独写「全文专用 prompt 函数」；在进入循环前 **合成单块**  
`{start_index: 0, end_index: n-1, summary: "全文"}`（或等价固定文案），后续与同路径一致。

### 2. 块边界上下文 Overlap（首版）

- **含义**：除本块正式覆盖的 `[start_index, end_index]` 外，在 prompt 中附带前后各 K 句，标为 **「仅上下文，不得写入 decisions」**，用于缓解块边界的指代、半截话误判。
- **首版约定**：**K = 0**（不附带）；跑通后若边界 bad case 多，再改为 K=1 或 K=2 并单测。

### 3. 合并规则与「带错重试 → 再失败则快速失败」

- **禁止静默覆盖**：合并各块 `decisions` 时，若同一 `index` 出现 **多于一条** `keep`，视为 **实现或模型越界输出**，**不得**后写覆盖前写。
- **带错重试（每块、每种错误类各最多一次）**：  
  - 第一次失败（含：重复 `index`、缺 index、越权 index、schema/解析失败等，按实现枚举）→ 第二次请求 **在同一用户消息中附带结构化错误说明**（例如：「上次输出中 index 7 出现两次；decisions 仅允许含 10–12」）。  
  - **若第二次仍失败**：此时 **「带错说明」已非空且已使用过**，**不再追加第三轮** → **整次 2b 快速失败**（`raise` 或统一错误码），由上层决定是否重跑 Layer2 或人工介入。
- **与普通 `call_llm_structured` / `call_turn_structured` 重试关系**：底层 JSON/网络重试可保留；上述「带错重试」是 **业务层多一轮**、且 **仅一轮**。

### 4. 无句面 / 空 `tokens` 禁止进入 2b

- 在 `run_2b_decision` 入口（或更早在 `run_intelligence_layer`）：`**tokens` 缺失、类型非法或 `len(tokens)==0`** 须在进 2a/2b 前 **直接拒绝**（`raise`），不调用 LLM、不写 `keep_mask`。（现行实现从清单 `**annotations[]`** 派生 `manifest["tokens"]`。）

### 5. 运行日志：推荐方案与记录内容

**推荐**：**标准库 `logging` + `FileHandler`**（必要时加 `RotatingFileHandler` 或按次运行生成独立文件，避免单文件无限增长）。

- **理由**：与现有 `intelligence_llm` 等模块的 `logging` 用法一致；可设独立 logger（如 `autosmartcut.run`）与独立文件，不污染全局根日志；后续若要 JSONL 或集中采集，再包一层 Handler 即可。
- **可选补充**：同一次 run 若需机器可读汇总，可 **额外** 写一行一条的 **JSONL**（与 FileHandler 并存）；非首版必选。

**建议每次执行（或每个输出目录一次 run）落盘一份日志，覆盖生命周期**，字段示例：


| 阶段     | 建议记录                                                                                              |
| ------ | ------------------------------------------------------------------------------------------------- |
| 元信息    | 时间、输入/输出路径、`goal`、run id                                                                          |
| Layer1 | 成功/失败、句数 `n`、`raw_text` 长度（若有）、关键字段是否存在                                                           |
| 2a     | R1/R2 成功/失败、各轮重试次数、`outline_blocks` 块数、纠错条数、稠密 `cleaned_annotations` 校验结果                         |
| 2b     | 有效块数（含合成「全文一块」）、每块 index 范围与句数、每块 LLM 成功/失败、带错重试是否触发、合并后 keep/cut 统计；若 API 返回 `usage` 则按调用累加或按块记录 |
| 2c/2d  | 占位或简要状态（启用后补全）                                                                                    |


### 6. 待办勾选（与 2b 实现绑定）

- 2b：按块循环 + 空块时合成单块；K=0；合并去重检测与带错重试一轮；第二次仍失败则快速失败。
- 无有效 `tokens` 时入口拒绝。
- 运行级日志：`logging` + `FileHandler`（路径/命名约定写入 CLAUDE 或 README 小节）；可选 JSONL。
- 单测：重复 index、越权 index、带错重试成功/失败路径。

---

## 文档对齐任务

- 主路径文档已与 **单清单 `timeline_manifest.json`**、内存 `**tokens[]**`（`index`+`text`）及 `**current.keep_mask**` 对齐；后续若 CLI 或字段变更，同步 README 与 MVP 文档族。
- 统一仓库文档命名：`outline_blocks`（理解分块）与 `**current.keep_mask**`（及历史文档中的 `segments.keep_mask` 嵌套表述）的术语使用。