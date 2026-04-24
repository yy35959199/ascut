# AutoSmartCut 架构

> 最后更新：2026-04-24

---

## 目录

1. [项目定义](#1-项目定义)
2. [名词表](#2-名词表)
3. [三层管道概述](#3-三层管道概述)
4. [PipelineSession 架构](#4-pipelinesession-架构)
5. [TimelineManifest 数据模型](#5-timelinemanifest-数据模型)
6. [代码结构](#6-代码结构)

---

## 1. 项目定义

AutoSmartCut 是一套针对时间轴媒体的语义处理管道。它把原始视频文件编译成一份携带完整语义标注的时间轴清单（TimelineManifest），再基于这份清单做剪辑决策，最终由执行层渲染为成片。视频文件是输入，TimelineManifest 才是核心资产。

---

## 2. 名词表

| 术语 | 说明 |
|------|------|
| **TimelineManifest** | 贯穿整条管道的中心数据结构，以 `timeline_manifest.json` 持久化。记录源媒体引用、句级标注、智能层产物和执行状态。 |
| **annotations[]** | 识别层产出的句级标注列表。每条包含 `index`（主坐标）、`t_start`/`t_end`（秒）、`content`（转写文字）、`gap_after`（句间间隔秒数）。 |
| **tokens[]** | 运行时派生，不落盘。由 `annotations[]` 的 `index` + `content` 构成，供 LLM Prompt 使用。 |
| **keep_mask[]** | 智能层 2b 产出的逐句保留决策，与 `annotations[]` 等长，每条为 `{"index": int, "keep": bool}`。 |
| **comprehension** | 智能层 2a 产出，包含 `purpose`（主旨）、`outline_blocks[]`（分块）、`corrections[]`（纠错映射）。 |
| **cleaned_annotations[]** | 运行时派生，不落盘。由程序根据 `corrections` 在 `tokens[].text` 上生成的消歧文本视图。 |
| **review_report** | 智能层 2c 产出，包含 `checklist[]`、`judgments[]`、`verdict`、`fix_instructions[]`。 |
| **PipelineSession** | DAG 调度核心，负责节点注册、拓扑排序、并行批次调度、EventBus、检查点管理。 |
| **StageNode** | 流水线阶段节点协议，每个节点声明 `id`、`reads`、`writes`、`phase`、`resumable`，实现 `run()` 和 `summarize()`。 |
| **StageContext** | 节点运行时上下文，包含 manifest 引用、config、emit 回调、pending_action 队列（仅 l2d_human）。 |
| **StageResult** | 节点 `run()` 的返回值，携带 `status`（SUCCESS/FAILED/REFLOW）、`summary`、`reflow_target`、`error`。 |
| **EventBus** | 推送模型事件总线，消费层通过 `subscribe(handler)` 注册，节点通过 `ctx.emit()` 发布事件。 |
| **FixedScheduler** | MVP 调度策略，按 DAG 拓扑序执行，内置 2b↔2c 循环规则和 2d 回流规则。 |
| **CLIAdapter** | CLI 消费层适配器，将 EventBus 事件格式化为文本打印，收到 `need_input` 时自动确认。 |
| **TUIAdapter** | TUI 消费层适配器，基于 Textual 框架，渲染三区域布局（侧边栏/主区域/日志区域）。 |
| **REFLOW** | 回流协议：l2d_human 节点返回 REFLOW 状态时，PipelineSession 重置目标节点及其下游并重新调度。 |
| **layer_status** | manifest 中记录各节点完成时间戳的字段，用于断点续跑判断。 |
| **run_id** | 单次流水线运行的唯一标识（ULID 格式）。 |

---

## 3. 三层管道概述

```
原始视频文件
    │
    ▼
┌─────────────────────────────────────────┐
│  Layer 1 — 识别层（Perception）           │
│  L1A: Qwen3-ASR-1.7B → 句级文本定稿      │
│  L1B: Qwen3-ForcedAligner → 补时间戳     │
│  产出：annotations[]（含 t_start/t_end）  │
└─────────────────────────────────────────┘
    │ annotations[]
    ▼
┌─────────────────────────────────────────┐
│  Layer 2 — 智能层（Intelligence）         │
│  2a: 理解（R1+R2 LLM + 程序消歧）         │
│  2b: 决策（LLM → keep_mask）             │
│  2c: 审核（LLM 两阶段 + 程序 verdict）    │
│  2d: 人工（交互 toggle + REFLOW）         │
│  产出：current.keep_mask（定稿）          │
└─────────────────────────────────────────┘
    │ annotations[] + keep_mask
    ▼
┌─────────────────────────────────────────┐
│  Layer 3 — 执行层（Execution）            │
│  keep_mask + 时间轴 → 保留区间            │
│  可选 VAD 切点吸附（Silero）              │
│  smartcut GOP 级精确剪切                  │
│  产出：最终视频文件                        │
└─────────────────────────────────────────┘
```

**关键约束：**
- L2 只消费 `annotations[]` 的 `index` 和 `content`，时间字段不进 LLM Prompt
- L3 读取 `annotations[]`（时间轴）+ `current.keep_mask`（决策），在执行层内部合成剪切区间
- EDL 不在 L2 落盘，由 L3 内部合成

智能层详细设计见 [intelligence.md](intelligence.md)。

---

## 4. PipelineSession 架构

### 4.1 分层架构

```
┌──────────────────────────────────────────────────────────┐
│                   消费层（Consumer Layer）                  │
│   CLIAdapter（打印事件）  │  TUIAdapter（Textual）           │
└─────────────────────────┬────────────────────────────────┘
                          │ subscribe() / send_action()
┌─────────────────────────▼────────────────────────────────┐
│                    PipelineSession                        │
│   EventBus  │  DAG 调度  │  Scheduler 委托  │  检查点管理  │
└─────────────────────────┬────────────────────────────────┘
                          │ next_action(snapshot)
┌─────────────────────────▼────────────────────────────────┐
│                  FixedScheduler                           │
│         按 DAG 拓扑序 + 2b↔2c 循环规则                     │
└─────────────────────────┬────────────────────────────────┘
                          │ run(ctx) / summarize(manifest)
┌─────────────────────────▼────────────────────────────────┐
│                  StageNode Layer（8 节点）                  │
│  L1aNode │ L1bNode │ L3PrecomputeNode                    │
│  L2aNode │ L2bNode │ L2cNode │ L2dNode │ L3Node          │
└─────────────────────────┬────────────────────────────────┘
                          │ reads / writes
┌─────────────────────────▼────────────────────────────────┐
│              TimelineManifest（共享存储）                   │
│                  timeline_manifest.json                   │
└──────────────────────────────────────────────────────────┘
```

### 4.2 DAG 节点拓扑

8 个节点通过 `reads`/`writes` 字段自动推导依赖关系：

| node_id | reads | writes | phase | resumable |
|---------|-------|--------|-------|-----------|
| `l1a_asr` | `source_media` | `annotations_l1a`, `raw_text` | 1 | False |
| `l1b_align` | `annotations_l1a`, `source_media` | `annotations` | 1 | True |
| `l3_precompute` | `annotations`, `source_media` | `sentence_tile_cache` | 1 | True |
| `l2a_comprehension` | `annotations_l1a`, `goal` | `comprehension` | 2 | True |
| `l2b_decision` | `comprehension`, `annotations_l1a` | `keep_mask` | 2 | True |
| `l2c_review` | `keep_mask`, `comprehension`, `annotations_l1a`, `goal` | `review_report` | 2 | True |
| `l2d_human` | `keep_mask`, `review_report`, `comprehension` | `human_feedback_history`, `l2d_completed` | 2 | True |
| `l3_execute` | `annotations`, `keep_mask`, `source_media`, `sentence_tile_cache`, `l2d_completed` | `output_video` | 3 | True |

> 注：`l2d_human` 的 `writes` 声明 `l2d_completed` 而非 `keep_mask`，原因见 [decisions.md D23](decisions.md#d23)。

**并行批次推导结果：**

| 批次 | 节点 | 前置条件 |
|------|------|---------|
| 批次 0 | `l1a_asr` | 无 |
| 批次 1 | `l1b_align`、`l2a_comprehension` | `l1a_asr` 完成 |
| 批次 2 | `l3_precompute`、`l2b_decision` | `l1b_align` 完成、`l2a_comprehension` 完成 |
| 批次 3 | `l2c_review` | `l2b_decision` 完成 |
| 批次 4 | `l2d_human` | `l2c_review` 完成 |
| 批次 5 | `l3_execute` | `l3_precompute` 完成、`l2d_human` 完成 |

`l2a_comprehension` 仅读取 `annotations_l1a`（L1A 产出），不依赖 `annotations`（L1B 产出），因此可与 `l1b_align` 并行。

**回流反向边**（不参与 DAG 拓扑排序，由 PipelineSession 动态处理）：
- `l2d_human` → REFLOW → `l2a_comprehension`（F1/F2 反馈）
- `l2d_human` → REFLOW → `l2b_decision`（F3 反馈）

### 4.3 EventBus 事件类型

| 事件类型 | 触发时机 | 关键字段 |
|---------|---------|---------|
| `stage_enter` | 节点开始执行 | `node_id`, `timestamp` |
| `stage_exit` | 节点执行完成 | `node_id`, `status`, `summary`, `timestamp` |
| `progress` | 节点执行过程中的进度更新 | `node_id`, `message` |
| `log` | 节点或 Session 产生日志 | `node_id`, `level`, `message` |
| `need_input` | l2d_human 需要人工输入 | `node_id`, `display`（DisplayData） |
| `error` | 节点执行失败 | `node_id`, `error` |
| `paused` | 流水线暂停 | `completed_nodes`, `checkpoint_path` |
| `pipeline_complete` | 流水线全部完成 | `output`（视频路径或 manifest 路径）, `elapsed_seconds` |

消费层通过 `session.subscribe(handler)` 注册处理器，可注册多个。

### 4.4 FixedScheduler 行为

FixedScheduler 实现 `Scheduler` 协议，`next_action(snapshot)` 的决策逻辑：

1. 无可调度节点且全部完成 → `COMPLETE`
2. 无可调度节点但有节点运行中 → 返回空 `RUN_BATCH`（等待）
3. `l2b_decision` 可调度且 `last_review_verdict == "fix_decision"` 且未超轮次 → `RUN_NODE(l2b_decision)` 并注入 `review_fixes`
4. `l2b_decision` 可调度且 `last_review_verdict == "fix_decision"` 且已达上限 → `RUN_NODE(l2b_decision)` 并注入 `force_pass=True`
5. 其余情况 → 注入 `two_b_mode`/`review_round` 参数，按节点数返回 `RUN_BATCH` 或 `RUN_NODE`

REFLOW 由 PipelineSession 的 `_handle_reflow()` 处理，FixedScheduler 不直接参与。

### 4.5 消费层适配器

**CLIAdapter**：
- 订阅 EventBus，将所有事件格式化为文本打印到标准输出
- 收到 `need_input` 事件时自动发送 `AcceptAction()`（auto 模式语义）
- `start_sync()` 内部调用 `asyncio.run(session.start_async())`

**TUIAdapter**：
- 基于 Textual 框架，三区域布局：侧边栏（节点状态）+ 主区域（进度/审阅）+ 日志区域
- 收到 `need_input` 事件时在主区域渲染 `ReviewScreen`
- `ReviewScreen` 解析命令（t/f1/f2/f3/f4/a/q/?），通过 `session.send_action()` 传递操作
- `PauseDialog` 提供三个选项：取消、强制中止并保存、等待当前阶段完成后暂停
- `start_async()` 并发运行 Textual App 和 PipelineSession

**PipelineSession 控制接口**：
- `pause()` — 设置暂停标志，当前节点完成后停止
- `abort(save=True)` — 立即停止，可选保存 manifest 检查点
- `resume()` — 重置标志，重新进入调度循环（已完成节点通过 layer_status 跳过）
- `send_action(action)` — 线程安全，将用户操作传递给等待中的 l2d_human 节点

---

## 5. TimelineManifest 数据模型

### 5.1 顶层字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `version` | string | 清单 schema 版本，如 `"1.0-mini"` |
| `run_id` | string | 单次运行唯一标识（ULID 格式） |
| `goal` | string | 用户传入的剪辑目标（`--goal` 参数） |
| `source_media` | object | 源媒体引用：`path`、`duration`（可选） |
| `annotations[]` | array | 句级标注列表（见 §5.3） |
| `current` | object | 智能层当前轮产物（见 §5.2） |
| `layer_status` | object | 各节点完成时间戳，用于断点续跑 |
| `raw_text` | string | L1A 产出的完整 ASR 原文 |
| `annotations_l1a` | array | L1A 产出的无时间轴标注（供 L2 和 L1B 并行读取） |
| `sentence_tile_cache` | string | L3Precompute 产出的 sidecar 目录路径 |
| `output_video` | string | L3 产出的视频文件路径 |

### 5.2 `current` 子结构

| 子字段 | 类型 | 说明 |
|--------|------|------|
| `comprehension` | object | 2a 产出：`purpose`（主旨）、`outline_blocks[]`（分块）、`corrections[]`（纠错映射） |
| `keep_mask[]` | array | 2b 产出（经 2d 确认后为定稿）：`[{"index": int, "keep": bool}, ...]`，与 `annotations[]` 等长 |
| `review_report` | object | 2c 产出：`checklist[]`、`judgments[]`、`verdict`、`fix_instructions[]`、`must_pass_rate` |

### 5.3 `annotations[]` 字段结构

每条标注：

| 字段 | 类型 | 说明 |
|------|------|------|
| `index` | int | 全局唯一序号，主坐标系统，L2 和 L3 均以此对齐 |
| `t_start` | float | 句级开始时间（秒），L1B 回填 |
| `t_end` | float | 句级结束时间（秒），L1B 回填 |
| `content` | string | 句级转写文字，L1A 写入后不再修改 |
| `gap_after` | float | 至下一句起点的间隔（秒），末句为媒体时长减当前句结束时间 |
| `confidence` | float | ASR 置信度 |
| `metadata` | object | 开放扩展字段，如 `char_timestamps[]` |

### 5.4 落盘 vs 运行时派生

| 数据 | 落盘 | 说明 |
|------|------|------|
| `annotations[]` | ✅ 是 | L1 写入，后续只读 |
| `current.comprehension` | ✅ 是 | 2a 写入（含 `corrections`） |
| `current.keep_mask[]` | ✅ 是 | 2b 写入，2d 确认后为定稿 |
| `current.review_report` | ✅ 是 | 2c 写入 |
| `human_feedback_history[]` | ✅ 是 | 2d 写入 |
| `layer_status` | ✅ 是 | 各节点完成时更新 |
| `tokens[]` | ❌ 否 | 运行时由 `annotations[]` 派生，保存前由 `strip_volatile_fields` 剥离 |
| `cleaned_annotations[]` | ❌ 否 | 运行时由程序根据 `corrections` 生成，保存前剥离 |
| EDL 时间区间 | ❌ 否 | L3 内部合成，不写入 manifest |

---

## 6. 代码结构

### 模块依赖方向（自上而下，无环）

```
runner.py                    # CLI 解析 + 流水线入口
  ├── pipeline_session.py    # DAG 调度核心
  │     ├── pipeline_scheduler.py   # FixedScheduler
  │     ├── pipeline_models.py      # 数据结构（StageResult, StageContext 等）
  │     ├── pipeline_events.py      # 事件数据类
  │     ├── pipeline_protocols.py   # StageNode / Scheduler 协议
  │     └── nodes/                  # 8 个节点实现
  │           ├── l1a_node.py       # → perception.py
  │           ├── l1b_node.py       # → perception.py
  │           ├── l3_precompute_node.py  # → l3_precompute.py
  │           ├── l2a_node.py       # → intelligence_2a.py
  │           ├── l2b_node.py       # → intelligence_2b.py
  │           ├── l2c_node.py       # → intelligence_2c.py
  │           ├── l2d_node.py       # → intelligence_2d_core.py
  │           └── l3_node.py        # → execution.py
  ├── cli_adapter.py         # CLI 消费层
  ├── tui_adapter.py         # TUI 消费层（Textual）
  ├── pipeline_run.py        # 单次运行元信息（run_id, manifest_path 等）
  ├── manifest_io.py         # manifest 读写（load / save / strip_volatile_fields）
  ├── manifest_stages.py     # --stage 解析与校验
  └── config.py              # AppConfig（从 config.toml 加载）

算法核心（不依赖 PipelineRun，不感知 CLI）：
  perception.py              # L1A/L1B 算法
  intelligence_2a.py         # 2a 理解
  intelligence_2b.py         # 2b 决策
  intelligence_2c.py         # 2c 审核
  intelligence_2d_core.py    # 2d 核心逻辑（toggle/merge/feedback）
  intelligence_2d_shell.py   # 2d TUI Shell（命令解析、格式化函数）
  intelligence_llm.py        # LLM 调用封装（多轮 API）
  execution.py               # L3 执行层
  l3_precompute.py           # L3 预计算（VAD + 候选接缝）
  annotation_tokens.py       # tokens_from_annotations（annotations → tokens）
  timeline_segments.py       # keep_mask → 时间区间（纯函数）
  vad_silence.py             # Silero VAD 切点吸附
```

### 关键文件职责

| 文件 | 职责 |
|------|------|
| `runner.py` | CLI 入口，解析参数，创建 PipelineSession，选择适配器 |
| `pipeline_session.py` | DAG 构建、调度循环、EventBus、REFLOW 处理、pause/abort/resume |
| `manifest_io.py` | manifest 的加载、保存（原子写）、volatile 字段剥离 |
| `manifest_stages.py` | `--stage` 参数解析、阶段合法性校验 |
| `pipeline_run.py` | 单次运行的操作元信息（run_id、路径、目标等） |
| `intelligence_2d_core.py` | 2d 业务逻辑（toggle/merge/feedback），不依赖 UI 框架 |
| `dual_track_merge.py` | 双轨 partial JSON 合并辅助（L1B 与 L2 并行时使用） |
