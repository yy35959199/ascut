# AutoSmartCut 架构

> 最后更新：2026-04-28  
> 本文档描述**当前实现**；编排为 **6 节点线性主链**，无 L1A/L1B 独立节点，无 L3 预处理 DAG 节点。

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

AutoSmartCut 是一套针对时间轴媒体的语义处理管道。它把原始视频编译成一份携带句级标注与智能层产物的**时间轴清单**（`timeline_manifest.json`），再基于清单做逐句保留决策，最后由执行层渲染为成片。**视频文件是输入，清单才是核心资产。**

---

## 2. 名词表

| 术语 | 说明 |
|------|------|
| **TimelineManifest** | 贯穿管道的中心数据，以 `timeline_manifest.json` 持久化。 |
| **annotations[]** | 识别层 `l1_perception` 产出的句级列表：`index`、`t_start`/`t_end`、`content`、`gap_after` 等。 |
| **tokens[]** | 由 `annotations[]` 派生的句面列表（`index` + `text`），供 L2 LLM Prompt；运行期写入 `manifest["tokens"]`，**可能**随 `PipelineSession` 落盘。非层间正式契约（权威句面仍以 `annotations[]` 为准），发布物可按需剔除。 |
| **keep_mask[]** | 智能层 `l2b_decision` 产出，与 `annotations[]` 等长：`{"index": int, "keep": bool}`。 |
| **comprehension** | 2a 产出：`purpose`、`outline_blocks[]`、程序生成的稠密 `cleaned_annotations[]`（见实现 `intelligence_2a`）。 |
| **review_report** | 2c 产出：`checklist[]`、`judgments[]`、程序计算的 `verdict`、`fix_instructions[]`、`must_pass_rate` 等。 |
| **PipelineSession** | DAG 调度、流式节点执行、EventBus、`REFLOW` 与 manifest 落盘。 |
| **StageNode** | 节点协议：`id`、`reads`、`writes`、`phase`、`resumable`，实现 `run()` / `summarize()`。 |
| **StageContext** | 节点上下文：manifest 字典、`config`、`emit()`、`params`（调度器注入）、`pending_action`（仅 2d 交互模式）。 |
| **StageResult** | `SUCCESS` / `FAILED` / `REFLOW` 及 `reflow_target` 等。 |
| **FixedScheduler** | 在 DAG 可调度前提下，处理 2b↔2c 内循环与 `force_pass` 注入。 |
| **CLIAdapter** | `ascut run` 默认：将事件打印到 stdout；异常收到 `need_input` 时自动 `AcceptAction`。 |
| **TUI** | `ascut tui`：`autosmartcut/tui/*`（Textual），通过同一 `PipelineSession` + `send_action` 交互。 |
| **REFLOW** | `l2d_human` 返回 `REFLOW` 时，Session 重置目标节点及下游并重新调度。 |
| **layer_status** | 记录各节点完成时间戳（键名形如 `{node_id}_completed_at`），用于 resumable 跳过。 |
| **run_id** | 单次工程 ULID，写入清单。 |

---

## 3. 三层管道概述

识别层、智能层、执行层在**产品语义**上仍为 L1→L2→L3；**编排实现**上由 6 个 `StageNode` 顺序衔接（依赖由 `reads`/`writes` 推导），**不存在**「L3 预处理」独立节点，也**不存在**将 L1 拆成两个并行 DAG 节点的当前形态。

```
原始视频
    │
    ▼
┌─────────────────────────────────────────┐
│  Layer 1 — 识别（节点 l1_perception）      │
│  分块 ASR + 强制对齐 → 句级 annotations[]   │
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
    │ annotations[] + keep_mask（及 current 同步）
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

- L2 Prompt 主要消费 **句面与时间无关字段**；时间轴在 `annotations[]` 中供 L3 使用。
- L3 校验并消费 **`annotations[]` + `current.keep_mask[]`**（见 `execution._validate_l3_manifest_for_execution`）。
- 剪切区间在 L3 内部合成，**不作为独立 EDL 字段落盘**。

智能层细节见 [intelligence.md](intelligence.md)。

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
│                  StageNode Layer（6 节点）                  │
│  L1Node │ L2aNode │ L2bNode │ L2cNode │ L2dNode │ L3Node  │
└─────────────────────────┬────────────────────────────────┘
                          │ reads / writes
┌─────────────────────────▼────────────────────────────────┐
│              TimelineManifest（共享存储）                   │
│                  timeline_manifest.json                   │
└──────────────────────────────────────────────────────────┘
```
```

### 4.2 DAG 节点拓扑（当前唯一形态）

`register_default_nodes()` 注册 **6 个节点**。依赖由 `reads`/`writes` 自动推导，主链为**线性**：L1 → L2a → L2b → L2c → L2d → L3。**无**与 L3 并行的预计算分支。

| node_id | reads | writes | phase | resumable |
|---------|-------|--------|-------|-----------|
| `l1_perception` | `source_media` | `annotations`, `raw_text` | 1 | False |
| `l2a_comprehension` | `annotations`, `goal` | `comprehension` | 2 | True |
| `l2b_decision` | `comprehension`, `annotations` | `keep_mask` | 2 | True |
| `l2c_review` | `keep_mask`, `comprehension`, `annotations`, `goal` | `review_report` | 2 | True |
| `l2d_human` | `keep_mask`, `review_report`, `comprehension` | `human_feedback_history`, `l2d_completed` | 2 | True |
| `l3_execute` | `annotations`, `keep_mask`, `source_media`, `l2d_completed` | `output_video` | 3 | True |

> `l2d_human` **不**在 `writes` 中声明 `keep_mask`（避免与 `l2b_decision` 双写冲突）；确认时仍可在 `run()` 内就地更新 `keep_mask`。`l3_execute` 通过读取 `l2d_completed` 与 `l2d_human` 建立边。详见 [decisions.md](decisions.md) 条目 **D23**。

**REFLOW**（动态处理，不进入静态 DAG 边）：

- F1/F2 → `reflow_target=l2a_comprehension`
- F3 → `reflow_target=l2b_decision`

### 4.3 EventBus 事件类型（摘要）

| 类型 | 说明 |
|------|------|
| `stage_enter` / `stage_exit` | 节点起止 |
| `progress` | 节点进度 |
| `log` | 日志 |
| `need_input` | 2d 等待人工（TUI 消费；CLI 通常不出现） |
| `error` | 失败 |
| `paused` | 暂停 |
| `pipeline_complete` | 流水线结束 |

### 4.4 FixedScheduler 行为（摘要）

1. 完成且无待运行 → `COMPLETE`
2. 有运行中任务 → 等待
3. `fix_decision` 且未超轮次 → 再次运行 `l2b_decision` 并注入 `review_fixes`
4. 达上限仍 `fix_decision` → `l2b_decision` 带 `force_pass=True`
5. 否则按 DAG 返回可运行批次并注入 `two_b_mode` / `review_round` 等

`REFLOW` 由 `PipelineSession._handle_reflow()` 处理。

### 4.5 L2 字段与 `current` 同步

节点往往写入 manifest **顶层**键（如 `keep_mask`、`comprehension`）。每次节点成功后，`PipelineSession._sync_to_current()` 将下列键复制到 `manifest["current"]`，以便 L3 与校验逻辑统一读取：

`keep_mask`、`comprehension`、`review_report`、`human_feedback_history`、`l2d_completed`。

### 4.6 控制接口（摘要）

- `subscribe` / `send_action`
- `pause` / `abort` / `resume`（如实现暴露）

---

## 5. TimelineManifest 数据模型

### 5.1 顶层字段（常用）

| 字段 | 说明 |
|------|------|
| `version` | 如 `1.0-mini`（`MANIFEST_VERSION`） |
| `run_id` | ULID |
| `goal` | 剪辑意图 |
| `source_media` | `path`、可选 `duration`、`audio_16k_path` 等 |
| `annotations[]` | 句级标注（L1） |
| `raw_text` | L1 完整 ASR 文本 |
| `current` | L2 产物镜像（见 §5.2） |
| `layer_status` | 各 `{node_id}_completed_at` |
| `output_video` | L3 输出路径 |
| `l3_segment_count` | L3 保留段数量（摘要用） |

历史或可选字段（若存在可被 `strip_volatile_fields` 从顶层移除）：`annotations_l1a`、`l1_contract`、`l1a_chunks` 等——**不作为当前编排契约**。

### 5.2 `current` 子结构

| 子字段 | 说明 |
|--------|------|
| `comprehension` | 2a：`purpose`、`outline_blocks[]`、`cleaned_annotations[]`（实现见 `intelligence_2a`） |
| `keep_mask[]` | 2b 决策；2d 确认后为定稿 |
| `review_report` | 2c |
| `human_feedback_history[]` | 2d |
| `l2d_completed` | 2d 完成标识（bool），供 L3 DAG 依赖 |

### 5.3 `annotations[]`（句级）

| 字段 | 说明 |
|------|------|
| `index` | 主坐标，与 `keep_mask[].index` 对齐 |
| `t_start` / `t_end` | 句级时间（秒），L1 对齐后写入 |
| `content` | 转写正文；**不在原句上被 L2 改写**（append-only 语义） |
| `gap_after` | 至下一句起点的间隔；末句按媒体时长推算 |
| `confidence` / `metadata` | 可选扩展（如字级时间戳） |

### 5.4 落盘与运行时字段

| 数据 | 说明 |
|------|------|
| `annotations[]`、`raw_text`、`current.*`（业务字段） | 正常落盘 |
| `tokens[]`（顶层）、`current.l2_checkpoints`、`current.tokens` 等 | 运行期或排障用。`manifest_io.strip_volatile_fields()` 会移除顶层历史键与 `current` 下若干瞬时键（含 `current.tokens`、`current.l2_checkpoints` 及 `current.comprehension.cleaned_annotations` 等），**不**删除顶层 `tokens[]`；发布前若需无句面投影请自行处理顶层 `tokens`（调用方决定是否对发布物执行） |

---

## 6. 代码结构

### 6.1 编排与用户入口

```
autosmartcut/runner.py              # CLI：ascut run | tui | resume
autosmartcut/app_controller.py      # SessionController / AppController
autosmartcut/session_factory.py     # build_session：PipelineRun + Session + Config
autosmartcut/cli_adapter.py         # CLI 事件打印
autosmartcut/tui/                   # Textual UI（app、screens、widgets）
autosmartcut/pipeline_session.py    # DAG、调度、REFLOW、落盘
autosmartcut/pipeline_scheduler.py  # FixedScheduler
autosmartcut/pipeline_models.py     # StageContext、StageResult 等
autosmartcut/pipeline_events.py     # 事件数据类
autosmartcut/pipeline_protocols.py  # StageNode / Scheduler 协议
autosmartcut/nodes/                  # l1_node … l3_node
autosmartcut/pipeline_run.py        # 新建 / 续跑 / 分叉、输出目录与日志名
autosmartcut/manifest_io.py         # load / save / strip_volatile_fields
autosmartcut/config.py              # AppConfig，默认读取仓库根 config.toml
```

### 6.2 算法与领域模块

```
autosmartcut/perception.py          # L1：run_l1_chunked 等
autosmartcut/intelligence_2a.py   # 2a
autosmartcut/intelligence_2b.py   # 2b
autosmartcut/intelligence_2c.py   # 2c
autosmartcut/intelligence_2d_core.py
autosmartcut/intelligence_llm.py
autosmartcut/execution.py           # L3：run_execution_layer
autosmartcut/timeline_segments.py
autosmartcut/vad_silence.py
autosmartcut/annotation_tokens.py
```

`dual_track_merge.py` 等为历史双轨辅助代码，**非当前主编排路径**。

### 6.3 关键文件职责

| 文件 | 职责 |
|------|------|
| `runner.py` | 子命令与参数解析，委托 `SessionController` / `AppController` |
| `pipeline_session.py` | DAG、执行循环、`_sync_to_current`、manifest 保存 |
| `manifest_io.py` | 原子写、字段剥离工具 |
| `pipeline_run.py` | `ascut_out_*` 目录、`run_*.log`、fork 新 `run_id` |
| `execution.py` | L3 输入校验、区间合成、smartcut 出片 |

---

## 7. 阶段参数 `--stage`（与 CLI 一致）

合法值仅：**`1`、`2`、`3`、`12`、`23`、`123`**。  
解析与校验见 `autosmartcut/runner.py` 与 `PipelineSession.parse_stage_arg()`。
