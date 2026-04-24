# AutoSmartCut

> 时间轴媒体语义处理管道：把原始视频编译为可持久化的**时间轴清单**（`timeline_manifest.json`），在清单上完成理解与逐句保留决策，再帧精确出片。

## 分发说明（请先读）

- 本仓库**默认不包含** `models/` 目录，**不提供**预下载的 Qwen 语音权重；首次使用识别层（L1）前，你必须自行从 **Hugging Face**（或 ModelScope / 离线拷贝等等价方式）拉取模型到本地，并在 `config.toml` 或 CLI 中指向正确路径。
- 层间主文件为 **`timeline_manifest.json`**（与 `autosmartcut.manifest_io.MANIFEST_FILENAME` 一致），清单内 `version` 常见为 **`1.0-mini`**。

---

## 核心思想

视频文件是输入，不是核心资产。**时间轴清单**才是。

系统将处理建模为「编译」：识别层把媒体变成句级 `annotations[]`；智能层在 **index** 主坐标上产出 `comprehension` 与 `keep_mask`；执行层把 `keep_mask` 还原为时间区间并调用 **smartcut** 做 GOP 级剪切。Layer1 写入的 `annotations[].content` **不被原地改写**；纠错由 LLM 输出坐标与意图，**程序**生成稠密消歧文本供 L2 使用。

---

## 架构总览

```text
原始视频 → [识别层 L1] → annotations[] → [智能层 L2] → keep_mask → [执行层 L3] → 成片
                              ↑                                            ↑
                     timeline_manifest.json（层间唯一主文件）
```

| 层 | 职责 | 核心产出 |
|----|------|---------|
| **L1 识别** | 解码音频 → ASR → 句级聚合（可拆分 **L1A** 仅文本 / **L1B** 仅对齐补时） | `annotations[]`（含 `gap_after`、字级时间戳） |
| **L2 智能** | 2a 理解 → 2b 决策 ↔ 2c 审核（内循环）→ 2d 人工 | `current.comprehension`、`current.keep_mask`、`current.review_report` |
| **L3 执行** | mask + 时间轴 → 保留区间 → 可选 VAD 吸附 → smartcut | 成片视频 |

流水线由 **PipelineSession** 驱动：8 个节点通过 `reads`/`writes` 声明数据依赖，DAG 自动推导并行能力（如 L1B 与 L2 在 L1A 完成后并行执行）。消费层通过 EventBus 订阅事件，支持 CLI 和 TUI（Textual）两种交互模式。

**智能层（最小认知）**

- **2a**：同一多轮会话上 **两次**结构化 LLM 调用（R1 粗理解、R2 精化+纠错），随后由**程序**根据 R2 的 `corrections` 生成稠密 `cleaned_annotations`（**仅内存**，供 2b）。
- **2b**：**一次**结构化 LLM 调用，输出与 `annotations[]` 等长的 `keep_mask`。支持 `single`（全文一次）和 `block`（按分块迭代）两种模式。
- **2c**：**一次**结构化 LLM 调用，两阶段输出（先生成 checklist，再逐条对照 keep_mask 判断），verdict 由程序计算。2b↔2c 内循环：审核未通过时重跑 2b 并注入修正指令，最多循环 `two_c_max_review_rounds` 轮（默认 1）。
- **2d**：默认自动确认（auto 模式）；加 `--interactive-2d` 或使用 `ascut tui` 可进入人工审阅，支持逐句 toggle、四种结构化反馈（F1 主旨偏差、F2 关键词纠错、F3 内容选择意见、F4 批量切换），F1/F2 回流至 2a，F3 回流至 2b。
- **LLM 调用次数（一次通过）**：**4 次**（2a×2 + 2b×1 + 2c×1）；2c 修正每轮额外 2 次。

保存清单前会执行 **`strip_volatile_fields`**（`autosmartcut/manifest_io.py`）：移除 **`tokens`**、**`cleaned_annotations`** 等不应长期落盘的键。

### 技术选型

| 环节 | 选型 | 说明 |
|------|------|------|
| 语音转写 | Qwen3-ASR（默认 1.7B） | 中文转写基线 |
| 字级对齐 | Qwen3-ForcedAligner-0.6B | 字级时间戳，服务精确切点 |
| 语义决策 | DeepSeek（OpenAI 兼容 HTTP API） | L2 理解、决策与审核 |
| 视频剪切 | smartcut | GOP 级 Remux，非切点区域零重编码损失 |
| 音频 | PyAV | 解码与 WAV 准备 |
| 切点吸附 | Silero VAD (ONNX) | 可选，改善接缝听感 |
| TUI | Textual | 人工审阅交互界面 |

---

## 环境要求

- **Python** ≥ 3.11
- **NVIDIA GPU + CUDA**（L1 ASR / 对齐推理）
- 网络以下载 Hugging Face 权重（或改用 ModelScope / 离线拷贝）

---

## 安装

在项目根目录打开终端（Windows 可用 **PowerShell** 或 **cmd**）：

```bash
pip install -e .
```

开发依赖（跑测试）：

```bash
pip install -e ".[dev]"
```

---

## Qwen 语音模型获取（默认无 `models/`）

官方 **Qwen3-ASR** 在 Hugging Face 上常列 **三个**相关模型仓（说明见 `https://huggingface.co/Qwen/Qwen3-ASR-1.7B`）：

| Hugging Face `repo_id` | 用途 |
|------------------------|------|
| `Qwen/Qwen3-ASR-1.7B` | 默认 ASR 主干（与本仓库默认 `config.toml` 一致） |
| `Qwen/Qwen3-ASR-0.6B` | 更小 ASR，与 **1.7B 二选一**（需自行改 `asr_model_path`） |
| `Qwen/Qwen3-ForcedAligner-0.6B` | 字级强制对齐（**必选**） |

**本仓库 `perception.py` 启动前只检查两个本地目录是否存在**：`asr_model_path` 与 `forced_aligner_path`。默认配置对应 **1.7B + ForcedAligner**。

### 推荐目录布局

在项目根创建 `models`（名称可自定，须与 `config.toml` 一致）。

```bash
mkdir -p models
```

### 使用 Hugging Face CLI 拉取

```bash
pip install "huggingface_hub[cli]"
```

若模型或组织需要 token：

```bash
huggingface-cli login
```

**默认本仓库所需（两条命令）**：

```bash
huggingface-cli download Qwen/Qwen3-ASR-1.7B --local-dir models/Qwen3-ASR-1.7B
huggingface-cli download Qwen/Qwen3-ForcedAligner-0.6B --local-dir models/Qwen3-ForcedAligner-0.6B
```

**可选第三条**（仅在希望改用更小 ASR 时）：

```bash
huggingface-cli download Qwen/Qwen3-ASR-0.6B --local-dir models/Qwen3-ASR-0.6B
```

若使用 0.6B ASR，请将 `config.toml` 中 `asr_model_path` 改为 `models/Qwen3-ASR-0.6B`（或你的实际路径）。

### 国内网络（可选）

若 Hugging Face 直连不稳定，可使用 **ModelScope** 或官方文档中的镜像说明，下载到**相同目录结构**；要点是本地必须是**完整模型目录**（含 `config.json`、权重分片等）。

---

## 配置 `config.toml`

在仓库根提供或复制一份 `config.toml`。**切勿将真实 API Key 提交到公共仓库**。

**`[models]`**（路径相对**当前工作目录**，建议使用绝对路径或始终在项目根执行）：

```toml
[models]
asr_model_path = "models/Qwen3-ASR-1.7B"
forced_aligner_path = "models/Qwen3-ForcedAligner-0.6B"
```

**`[llm]`**（DeepSeek 示例；`base_url` 以服务商说明为准）：

```toml
[llm]
api_key = "your-api-key"
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
```

官方 API 文档：<https://api-docs.deepseek.com/zh-cn/>

完整配置项（`[perception]`、`[intelligence]`、`[execution]`）见 [doc/cli-and-config.md](doc/cli-and-config.md#3-configtoml-配置项)。

---

## 快速开始

**全流程（L1→L2→L3）**：

```bash
ascut run --input video.mp4 --goal "保留核心内容，删除口误和冗余" --stage 123
```

**仅识别**：

```bash
ascut run --input video.mp4 --stage 1
```

**从已有清单跑智能 + 执行**：

```bash
ascut run --manifest path/to/timeline_manifest.json --goal "你的剪辑意图" --stage 23
```

**仅重跑执行层**（例如调整 padding）：

```bash
ascut run --manifest path/to/timeline_manifest.json --stage 3 --pre-pad 0.2 --post-pad 0.3
```

**TUI 交互模式（人工审阅）**：

```bash
ascut tui --input video.mp4 --goal "精华版，压缩至原时长 60%" --stage 123
```

**CLI 模式启用人工审阅**：

```bash
ascut run --input video.mp4 --goal "..." --stage 123 --interactive-2d
```

首次含 L1 且未指定 `--output-dir` 时，默认输出目录为**视频同目录下的 `ascut_out_<ULID 前 8 位>`**；清单为 **`{output_dir}/timeline_manifest.json`**。

完整 CLI 参数、`--stage` 所有合法值及使用示例见 [doc/cli-and-config.md](doc/cli-and-config.md)。

---

## `timeline_manifest.json`（落盘形态示例）

写入磁盘前会剥离运行时字段；以下为**典型落盘**结构示意。`keep_mask` 与 `annotations` **条数相同、`index` 对齐**。

```json
{
  "version": "1.0-mini",
  "run_id": "01KNY5F2XXXXXXXX",
  "goal": "保留核心内容",
  "source_media": { "path": "video.mp4", "duration": 180.5 },
  "annotations": [
    {
      "index": 0,
      "t_start": 0.0,
      "t_end": 15.3,
      "content": "大家好，今天我们来聊深度学习。",
      "gap_after": 1.8,
      "confidence": 0.91,
      "metadata": {}
    }
  ],
  "current": {
    "comprehension": {
      "purpose": "讲解深度学习在视频处理中的三种应用路径",
      "outline_blocks": [
        { "start_index": 0, "end_index": 12, "summary": "开场与背景介绍" }
      ],
      "corrections": [
        { "index": 5, "old": "深度血习", "nth": 1, "new": "深度学习" }
      ]
    },
    "keep_mask": [
      { "index": 0, "keep": true },
      { "index": 1, "keep": false }
    ],
    "review_report": {
      "verdict": "pass",
      "checklist": [],
      "judgments": [],
      "fix_instructions": [],
      "must_pass_rate": "3/3"
    }
  },
  "layer_status": {
    "l1a_asr_completed_at": "2026-04-24T10:00:00",
    "l1b_align_completed_at": "2026-04-24T10:01:00",
    "l2a_comprehension_completed_at": "2026-04-24T10:02:00",
    "l2b_decision_completed_at": "2026-04-24T10:03:00",
    "l2c_review_completed_at": "2026-04-24T10:03:30",
    "l2d_human_completed_at": "2026-04-24T10:03:31"
  }
}
```

**说明**：运行时 `comprehension` 内可能含程序生成的稠密 `cleaned_annotations`，保存前会被移除。完整数据模型见 [doc/architecture.md](doc/architecture.md#5-timelinemanifest-数据模型)。

---

## 设计原则

- **清单为中心**：层间通过 `timeline_manifest.json` 字段约定耦合，各层实现可替换。
- **Append-only**：不原地改写 L1 的 `annotations[].content`；消歧通过 `cleaned_annotations` 旁路表达。
- **index 主坐标**：L2 决策对齐句级序号；时间还原在 L3。
- **消歧确定性**：LLM 输出纠错意图（`corrections`），程序生成稠密消歧视图供 2b 使用。
- **DAG 自动并行**：节点声明 `reads`/`writes`，PipelineSession 自动推导依赖与并行批次。
- **事件驱动消费**：CLI/TUI/未来 GUI 通过 EventBus 订阅事件，不耦合调度逻辑。

---

## 文档导航

| 文档 | 内容 | 适合谁 |
|------|------|--------|
| [doc/architecture.md](doc/architecture.md) | 系统架构、DAG 8 节点拓扑、TimelineManifest 数据模型、代码结构 | 新加入的开发者，想理解系统全貌 |
| [doc/intelligence.md](doc/intelligence.md) | 智能层四子阶段详细设计：LLM 调用模式、数据契约、2b↔2c 内循环、2d REFLOW 协议 | 修改 L2 代码的开发者 |
| [doc/cli-and-config.md](doc/cli-and-config.md) | 完整 CLI 参数表、`--stage` 所有合法值、`config.toml` 全部配置项、使用示例 | 用户、运维 |
| [doc/decisions.md](doc/decisions.md) | 技术决策记录（ADR）：为什么选 smartcut、为什么虚拟消歧、为什么 DAG 调度等 | 做架构决策的人 |
| [doc/future-roadmap.md](doc/future-roadmap.md) | 未来升级方向：插件化、Agent 调度、GUI、流式处理等 | 规划者 |

---

## 测试

```bash
pytest
```

---

## 常见问题

| 现象 | 处理方向 |
|------|----------|
| `ASR 模型目录不存在` / `Forced aligner 目录不存在` | 确认已按上文下载完整目录；检查 `config.toml` 与当前工作目录；或改用 `--asr-model` / `--forced-aligner` 绝对路径 |
| CUDA OOM | 尝试 `--dtype bfloat16`、更小 ASR（0.6B）、或 `--backend vllm`（需环境支持） |
| L2 API 报错 | 检查 `api_key`、`base_url`、网络；核对服务商模型名 |
| `--stage 23` 报错 | 确认清单已有 `annotations` 与合法的 `current.keep_mask`（长度与句数一致） |
| 2c 审核循环不停 | 检查 `two_c_max_review_rounds` 配置（默认 1，设为 0 可关闭审核） |
| 2d 回流无效 | 检查 `two_d_max_reflows` 配置（默认 3，设为 0 可禁用回流） |

---

## License

MIT
