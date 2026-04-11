# AutoSmartCut — MVP-mini 方案说明

> 本文档描述 **MVP-mini**：以 **单一 `timeline_manifest.json`（TimelineManifest）** 为唯一持久化载体，贯通 L1→L2→L3；**不**以 JSON1 / JSON2 / JSON3 作为正式层间契约，**不**为兼容旧路径而长期保留双写或平行主 API。  
> 架构愿景见 [AutoSmartCut.md](AutoSmartCut.md)；全局 MVP 与检查点叙事见 [AutoSmartCut-MVP.md](AutoSmartCut-MVP.md)（§10 为摘要，**字段级最小落盘与实现顺序以本文为准**）；2a/2b 的 LLM 契约见 [intelligence-layer2-mvp.md](intelligence-layer2-mvp.md)。

---

## 目录

1. [文档定位](#1-文档定位)
2. [术语与命名约定](#2-术语与命名约定)
3. [MVP-mini 范围与边界](#3-mvp-mini-范围与边界)
4. [TimelineManifest 数据模型](#4-timelinemanifest-数据模型)
5. [落盘 vs 运行时派生](#5-落盘-vs-运行时派生)
6. [清单生命周期（按阶段）](#6-清单生命周期按阶段)
7. [Layer 1 / 2 / 3 与清单的契约](#7-layer-1--2--3-与清单的契约)
8. [编排与 CLI：`--stage` 与 `--from-stage`](#8-编排与-cli---stage-与---from-stage)
9. [续跑与分叉（`--output-dir`）](#9-续跑与分叉---output-dir)
10. [产物](#10-产物)
11. [与 `intelligence-layer2-mvp.md` 的差异](#11-与-intelligence-layer2-mvpmd-的差异)
12. [迁移策略：`demos` / 测试 / 文档同一批次](#12-迁移策略demos--测试--文档同一批次)
13. [变更后的代码结构（高内聚、低耦合）](#13-变更后的代码结构高内聚低耦合)
14. [实现顺序（非规范）](#14-实现顺序非规范)

---

## 1. 文档定位

- 本文是 **MVP-mini 的实现与评审依据**：单清单、最小落盘、`--stage` 编排；**不**重复展开 2a/2b 的 Prompt、Schema、多轮 API（仍以 `intelligence-layer2-mvp.md` 与 `intelligence_*.py` 为准）。
- **与 `AutoSmartCut-MVP.md` §10 的关系**：§10 保留总览与检查点目录叙事；若表格与本文在「是否落盘某字段」上不一致，**以本文 §4–§5 为准**。

---

## 2. 术语与命名约定

| 名称 | 含义 |
|------|------|
| **TimelineManifest** | 磁盘上的 **`timeline_manifest.json`**；全管道 SSOT。 |
| **`annotations[]`** | L1 句级事实；主坐标 `index`；智能层**只读**，不覆写 `content`。 |
| **`tokens[]`（运行时）** | 由 `annotations[]` 派生（`index` + `text`），**不落盘**。 |
| **`cleaned_annotations[]`（运行时）** | 由 `annotations[]` + `current.comprehension`（含 `corrections` 等）程序确定性生成，**不落盘**（与现行 layer2 文档中「写入 comprehension」的叙事区分：MVP-mini 最小落盘）。 |
| **`current.comprehension`** | 2a 落盘：`purpose`、`outline_blocks[]`、`corrections[]` 等（与现行代码结构对齐）。 |
| **`current.keep_mask[]`** | 2b 落盘；MVP-mini 无 overrides 时 **L3 直接消费**，不单独持久化 `keep_mask_final[]`。 |
| **透传（2c / 2d）** | 仍**调用**对应入口；不调 LLM、不改变 `keep_mask`（与 §3 一致）。 |

---

## 3. MVP-mini 范围与边界

### 3.1 范围内

- 单一 **`timeline_manifest.json`**；正式路径**不**依赖 `layer1_annotations.json` / `layer2_input.json` / `layer2_output.json`。
- **`ascut run --stage`**：`1` | `2` | `3` | `12` | `23` | `123`；默认 **`123`**。
- **`--from-stage`**：仅作 **deprecated 别名**，映射为「从该层起到第 3 层」的等价 `--stage`（见 §8），不单独维护一套编排语义。
- 智能层：`2a → 2b → 2c（透传）→ 2d（透传或 auto 跳过交互）`；LLM 次数与现行 MVP 一致。

### 3.2 范围外

- 多轮闭环、`previous` / `history_summary`：见 [AutoSmartCut.md §11.5](AutoSmartCut.md#115-循环推进与历史裁剪)。
- 真实 2c LLM、2d 自然语言回流、checklist 主流程：见 [intelligence-layer2-mvp.md](intelligence-layer2-mvp.md) §3.2。

---

## 4. TimelineManifest 数据模型

### 4.1 顶层

| 字段 | 说明 |
|------|------|
| `version` | 如 `"1.0-mini"`。 |
| `run_id` | ULID 等。 |
| `goal` | 智能层目标。 |
| `source_media` | `path`、`duration`（可选）等。 |
| `annotations[]` | 与现行 L1 句级数组同构。 |
| `current` | 见 §4.2。 |
| `layer_status` | 可选：`l1_completed_at` / `l2_completed_at` / `l3_completed_at`。 |

### 4.2 `current`（单轮）

| 字段 | 说明 |
|------|------|
| `comprehension` | 2a：`purpose`、`outline_blocks`、`corrections` 等。 |
| `keep_mask` | 2b：`[{ "index", "keep" }, ...]`，与 `annotations` 等长。 |

可选：`round`、`timestamp`。**不**要求清单内持久化 `tokens`、`cleaned_annotations`、独立 `keep_mask_final`。

---

## 5. 落盘 vs 运行时派生

| 数据 | 落盘 |
|------|------|
| `annotations[]`、`comprehension`（含 `corrections`）、`keep_mask` | 是 |
| `tokens[]`、`cleaned_annotations[]`、EDL 时间区间 | 否 |

---

## 6. 清单生命周期（按阶段）

1. 编排创建骨架：`version`、`run_id`、`goal`、`source_media`、`annotations` 空或缺省、`current` 空。  
2. L1 结束：写 `annotations[]`，更新 `layer_status`。  
3. 2a 结束：写 `current.comprehension`（不写 `cleaned_annotations` 到磁盘）。  
4. 2b 前：内存生成 `tokens`、`cleaned_annotations`，跑 LLM。  
5. 2b 结束：写 `current.keep_mask`。  
6. 2c→2d：调用透传实现；不改 `keep_mask`。  
7. L3：读 `annotations` + `current.keep_mask`；出片；更新 `layer_status`。

保存前可对 dict 做 **`strip_volatile_fields`**，避免误写运行时键。

---

## 7. Layer 1 / 2 / 3 与清单的契约

- **L1**：读视频；写 `annotations`（及 `source_media` / 顶层 `source` 二选一须在实现中固定 SSOT）；**不**把 JSON2 作为正式产物。  
- **L2**：读清单；内存注入 `tokens`；写 `current`；**不**写 JSON3 文件。  
- **L3**：读 `annotations` + `current.keep_mask`；`keep_mask_to_positive_segments` 等与现行一致；成片路径由 CLI 约定。

---

## 8. 编排与 CLI：`--stage` 与 `--from-stage`

### 8.1 `--stage`（正式）

合法 **`SPEC`**：`1`、`2`、`3`、`12`、`23`、`123`（有序、连续子集）。默认 **`123`**。

解析为集合 `stages ⊆ {1,2,3}` 后：

```text
if 1 in stages: run_perception_layer(...)
if 2 in stages: run_intelligence_layer(...)
if 3 in stages: run_execution_layer(...)
```

**与 `--manifest` / `--input` 的交叉约束**（与先前方案一致）：

- `SPEC` **以 `1` 开头**：必须 `--input`；**禁止** `--manifest`（本次创建清单）。  
- **不以 `1` 开头**（`2` / `3` / `23`）：必须 **`--manifest`**；禁止 `--input`。  
- `2 ∈ stages`：`annotations` 非空。  
- `3 ∈ stages`：存在 `current.keep_mask` 且与 `annotations` 等长。

### 8.2 `--from-stage`（仅兼容别名）

| `--from-stage` | 等价 `--stage` |
|----------------|----------------|
| `1` | `123` |
| `2` | `23` |
| `3` | `3` |

**规则**：`--stage` 与 `--from-stage` **不得同时指定**；若仅用 `--from-stage`，先映射为 `SPEC` 再走与 §8.1 完全相同的校验与执行。**实现上只保留一套分支逻辑**。

---

## 9. 续跑与分叉（`--output-dir`）

- **续跑**：已指定 `--manifest`，且未指定 `--output-dir` 或 `--output-dir` 与 manifest **所在目录相同** → 原地更新同一文件（`run_id` 不变）。  
- **分叉**：`--output-dir` 指向**另一目录** → **仅拷贝** `timeline_manifest.json` 到新目录、分配新 `run_id` 后再写入；源视频路径仍指向原媒体文件（不复制视频本体）。

---

## 10. 产物

- **`timeline_manifest.json`**：`stages` 含 `1` 或 `2` 时持续更新；含 `3` 时可更新 `layer_status`。  
- **成片视频**：仅当 `3 ∈ stages`。

---

## 11. 与历史「三 JSON 教具」的对照（非现行契约）

以下仅便于对照**旧版独立文件**；**现行工程**以本文 §4–§7 与 [intelligence-layer2-mvp.md](intelligence-layer2-mvp.md)（已修订）为准。

| 主题 | 历史独立文件 | 现行 `timeline_manifest.json` |
|------|----------------|------------------------------|
| L2 句面 | JSON2 `tokens[]` | 由 `annotations[]` **内存**派生 `tokens[]`，**不落盘** |
| `cleaned_annotations` | 常与 comprehension 同述 | **默认不落盘**；保存前 `strip_volatile_fields` |
| `corrections` | — | **落入** `current.comprehension` 以便程序重算 `cleaned` |
| L2 对外决策 | JSON3 | **`current.keep_mask`** |
| L3 输入 | JSON1 + JSON3 | **`annotations[]` + `current.keep_mask`**（同文件） |

不变：2a/2b LLM 轮次、index 主坐标、`keep` 布尔、EDL 不在 L2 落盘。

---

## 12. 迁移策略：`demos` / 测试 / 文档同一批次

**原则：不为「旧 Demo 仍能走三 JSON 主路径」而长期保留平行实现。**

| 范围 | 要求 |
|------|------|
| **`demos/`** | `demo1_asr.py`、`demo2_llm.py`、`demo3_smartcut.py`、`demo_layer2_input_2ab_compare.py`、`demos/tools/*.py` 与 **新 CLI / 清单**对齐：示例参数改为 `--manifest` + `--stage`；若某脚本仅为「生成旧 JSON 教具」，改为 **生成或填充 `timeline_manifest.json`**，或移入 `tests/fixtures/` 由测试专用。 |
| **`README.md`、本文档族、`runner` 模块文档字符串** | 删除以 `--layer*-json` / `--from-stage` 为**主叙事**的表述；`--from-stage` 仅标注 deprecated。 |
| **测试** | 新主路径用 manifest fixture；**不**要求保留「必须通过写 layer2_input.json 才能测 L2」的结构性依赖。 |
| **代码** | **不**保留「正式 `PipelineRun` 仍携带 `json1_path` + `tokens_json_path` + `json3_path`」的长期双轨；过渡期如需 git bisect，以**短分支 / 单 PR 系列**解决，而非在 `main` 上叠床架屋的兼容层。 |

---

## 13. 变更后的代码结构（高内聚、低耦合）

**依赖方向（自上而下，无环）：**

```
runner.py                 # 仅 CLI 解析 + resolve_stages + 调用各 layer 入口
  ├── manifest_stages.py  # parse_stage_spec、resolve_stages、validate_cli_args（无 I/O）
  ├── pipeline_run.py     # run_id、manifest_path、output_dir、output_video 等运行元数据
  ├── manifest_io.py      # load / save / skeleton / strip_volatile（仅 JSON 与路径）
  ├── perception.py       # L1：算法 → 写清单
  ├── intelligence.py
  └── execution.py
```

**算法核心（不依赖 `PipelineRun`、不感知 CLI）：**

- `perception.py` — ASR、对齐、句级聚合等；**写** `timeline_manifest.json` 中 `annotations[]`。
- `intelligence_2a.py` / `intelligence_2b.py` / `intelligence_2c.py` / `intelligence_2d.py` / `intelligence_llm.py` — 入参出参仍为 **`manifest_dict`**。
- `timeline_segments.py`、`execution.py` 内 **纯函数**（如 `keep_mask_to_positive_segments`）— 由 `execution.py` 的 `run_execution_layer` 组装 I/O。

**数据工具：**

- `annotation_tokens.py`：`tokens_from_annotations`、`validate_tokens`、`video_path_from_manifest`；**不**再以「从磁盘加载 JSON2」作为公共主 API（原 `layer2_tokens.py` 已移除）。

**内聚含义**：清单读写只出现在 `manifest_io.py`；阶段合法性只出现在 `manifest_stages.py`；各层模块只做「读清单 → 调核心 → 写清单/出片」。**耦合点**仅为：各层认同一套 manifest 字段路径（由本文 §4–§7 约定）。

---

## 14. 实现顺序（非规范）

1. `manifest_io.py`、`manifest_stages.py`、`annotation_tokens.py`  
2. `pipeline_run.py` 以 `manifest_path` 为轴  
3. `perception.py` / `intelligence.py` / `execution.py`  
4. `runner.py`：`--stage`、`--manifest`、`--input`、条件执行 L1/L2/L3  
5. **同一 PR 或紧接 PR**：更新 `demos/`、`tests/`、`README.md`、`AutoSmartCut-MVP.md` §7  
6. 删除三 JSON 主路径与旧 `PipelineRun` 多文件字段  

---

*文档版本：1.0.1*  
*状态：MVP-mini（单清单 + `--stage` + Demos/文档同批迁移；分叉仅拷贝清单 JSON）*
