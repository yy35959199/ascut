# AutoSmartCut

> 时间轴媒体语义处理管道：把原始视频编译为可持久化的**时间轴清单**（`timeline_manifest.json`），在清单上完成理解与逐句保留决策，再帧精确出片。

## 分发说明（请先读）

- 本仓库**默认不包含** `models/` 目录，**不提供**预下载的 Qwen 语音权重；首次使用识别层（L1）前，你必须自行从 **Hugging Face**（或 ModelScope / 离线拷贝等等价方式）拉取模型到本地，并在 `config.toml` 或 CLI 中指向正确路径。
- 层间主文件为 **`timeline_manifest.json`**（与 `autosmartcut.manifest_io.MANIFEST_FILENAME` 一致），清单内 `version` 常见为 **`1.0-mini`**。

---

## 核心思想

视频文件是输入，不是核心资产。**时间轴清单**才是。

系统将处理建模为「编译」：识别层把媒体变成句级 `annotations[]`；智能层在 **index** 主坐标上产出 `comprehension` 与 `keep_mask`；执行层把 `keep_mask` 还原为时间区间并调用 **smartcut** 做 GOP 级剪切。`annotations[].content` **不被原地改写**；消歧由 2a 产出稠密 `cleaned_annotations` 等旁路字段供 2b 使用。

---

## 架构总览（当前实现）

```text
原始视频 → [L1 l1_perception] → annotations[] → [L2: 2a→2b↔2c→2d] → keep_mask → [L3 l3_execute] → 成片
                              ↑                                              ↑
                     timeline_manifest.json（层间唯一主文件）
```

| 层 | 编排节点 | 职责摘要 | 核心产出 |
|----|----------|----------|----------|
| **L1** | `l1_perception` | 分块 ASR + 强制对齐 → 句级时间与文本 | `annotations[]`、`raw_text` |
| **L2** | `l2a` → `l2b` ↔ `l2c` → `l2d` | 理解 → 决策 ↔ 审核 → 人工定稿 | `comprehension`、`keep_mask`、`review_report`（经 `current` 同步） |
| **L3** | `l3_execute` | 清单 → 保留区间 → 可选 VAD → smartcut | `output_video` |

流水线由 **`PipelineSession`** 驱动：默认注册 **6 个**节点，通过 `reads`/`writes` 推导 DAG；主链为 **L1→L2→L3 线性**，**无**独立的「L1A/L1B」编排节点，**无**「L3 预处理」DAG 节点。消费层通过 EventBus：`ascut run` 使用 **CLIAdapter**；`ascut tui` 使用 **Textual**（`autosmartcut/tui`）。

**智能层（摘要）**

- **2a**：两轮结构化 LLM（R1 粗理解、R2 精化 + 纠错表）+ 程序生成稠密 `cleaned_annotations`。
- **2b**：结构化 LLM → `keep_mask`；`single` / `block` 模式见配置。
- **2c**：单次 LLM 两阶段（checklist → judgments），`verdict` 由程序计算；与 2b 内循环受 `two_c_max_review_rounds` 约束。
- **2d**：无交互队列时自动确认；`ascut tui` 或 `ascut run --interactive-2d` 可人工审阅与 REFLOW。

可选在发布前调用 **`strip_volatile_fields`**（`manifest_io.py`）剥离部分瞬时字段：顶层历史键（如 `l1a_chunks`、`annotations_l1a` 等）、`current.l2_checkpoints`、`current.tokens`，以及 `current` 内 `comprehension.cleaned_annotations` 等（见实现）。**注意**：当前实现**不会**删除顶层 `manifest["tokens"]`；若发布物需完全无 `tokens`，请在保存前自行 `pop("tokens")` 或扩展剥离逻辑（按你的发布策略决定）。

### 技术选型

| 环节 | 选型 | 说明 |
|------|------|------|
| 语音转写 | Qwen3-ASR（默认 1.7B） | 中文转写基线 |
| 字级对齐 | Qwen3-ForcedAligner-0.6B | 字级时间戳 |
| 语义决策 | DeepSeek（OpenAI 兼容 HTTP API） | L2 |
| 视频剪切 | smartcut | GOP 级 Remux |
| 音频 | PyAV | 解码与 WAV |
| 切点吸附 | Silero VAD (ONNX) | 可选 |
| TUI | Textual | 人工审阅 |

---

## 环境要求

- **Python** ≥ 3.11
- **NVIDIA GPU + CUDA**（L1 推理）
- 网络以下载 Hugging Face 权重（或 ModelScope / 离线拷贝）

---

## 安装

在项目根目录（含 `pyproject.toml`）打开 **PowerShell** 或 **cmd**：

```bash
pip install -e .
```

开发依赖（跑测试）：

```bash
pip install -e ".[dev]"
```

---

## Qwen 语音模型获取（默认无 `models/`）

官方 **Qwen3-ASR** 在 Hugging Face 上常列三个相关模型仓（说明见 <https://huggingface.co/Qwen/Qwen3-ASR-1.7B>）：

| Hugging Face `repo_id` | 用途 |
|------------------------|------|
| `Qwen/Qwen3-ASR-1.7B` | 默认 ASR 主干（与默认 `config.toml` 一致） |
| `Qwen/Qwen3-ASR-0.6B` | 更小 ASR，与 1.7B 二选一（需改 `asr_model_path`） |
| `Qwen/Qwen3-ForcedAligner-0.6B` | 字级强制对齐（必选） |

`perception.py` 启动前检查本地目录：`asr_model_path` 与 `forced_aligner_path`。

### 推荐目录布局

在项目根创建文件夹 `models`（名称可自定，须与 `config.toml` 一致）。PowerShell 示例：

```powershell
New-Item -ItemType Directory -Force models
```

### 使用 Hugging Face CLI 拉取

```bash
pip install "huggingface_hub[cli]"
```

若需 token：

```bash
huggingface-cli login
```

**默认所需（两条命令）**：

```bash
huggingface-cli download Qwen/Qwen3-ASR-1.7B --local-dir models/Qwen3-ASR-1.7B
huggingface-cli download Qwen/Qwen3-ForcedAligner-0.6B --local-dir models/Qwen3-ForcedAligner-0.6B
```

**可选第三条**（改用更小 ASR）：

```bash
huggingface-cli download Qwen/Qwen3-ASR-0.6B --local-dir models/Qwen3-ASR-0.6B
```

### 国内网络（可选）

可使用 ModelScope 或镜像，下载为**完整模型目录**（含 `config.json`、权重分片等）。

---

## 配置 `config.toml`

在**仓库根**提供或复制 `config.toml`。**切勿将真实 API Key 提交到公共仓库**。

默认加载路径为仓库根 `config.toml`；也可用 `--config` 指定。

**`[models]`**（路径相对当前工作目录，建议绝对路径或在项目根执行）：

```toml
[models]
asr_model_path = "models/Qwen3-ASR-1.7B"
forced_aligner_path = "models/Qwen3-ForcedAligner-0.6B"
```

**`[llm]`**（DeepSeek 示例）：

```toml
[llm]
api_key = "your-api-key"
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
```

官方 API 文档：<https://api-docs.deepseek.com/zh-cn/>

完整 CLI 与 `[perception]` / `[intelligence]` / `[execution]` 见 [doc/cli-and-config.md](doc/cli-and-config.md)。

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
ascut run --manifest path\to\timeline_manifest.json --goal "你的剪辑意图" --stage 23
```

**仅重跑执行层**（例如调整 padding）：

```bash
ascut run --manifest path\to\timeline_manifest.json --stage 3 --pre-pad 0.2 --post-pad 0.3
```

**TUI**：

```bash
ascut tui path\to\video.mp4
```

**CLI 启用 2d 交互队列**：

```bash
ascut run --input video.mp4 --goal "..." --stage 123 --interactive-2d
```

首次含 L1 且未指定 `--output-dir` 时，默认输出目录为**视频同目录下的 `ascut_out_<YYYY-mm-DD_HH-MM-ss.SSS>`**（冲突 `_01`…）；清单为 **`{output_dir}\timeline_manifest.json`**；日志为 **`run_<YYYY-mm-DD_HH-MM-ss.SSS>.log`**；**`run_id` 为 ULID**。

`--stage` **仅**支持：`1`、`2`、`3`、`12`、`23`、`123`。详见 [doc/cli-and-config.md](doc/cli-and-config.md)。

---

## `timeline_manifest.json`（落盘示意）

以下为常见字段示意；`keep_mask` 与 `annotations` **条数相同、index 对齐**。`layer_status` 键名为 **`{node_id}_completed_at`**（与实现一致）。

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
      "purpose": "讲解深度学习在视频处理中的应用",
      "outline_blocks": [
        { "start_index": 0, "end_index": 12, "summary": "开场与背景" }
      ],
      "cleaned_annotations": [
        { "annotation_index": 0, "cleaned_content": "大家好，今天我们来聊深度学习。" }
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
    "l1_perception_completed_at": "2026-04-24T10:00:00Z",
    "l2a_comprehension_completed_at": "2026-04-24T10:02:00Z",
    "l2b_decision_completed_at": "2026-04-24T10:03:00Z",
    "l2c_review_completed_at": "2026-04-24T10:03:30Z",
    "l2d_human_completed_at": "2026-04-24T10:03:31Z",
    "l3_execute_completed_at": "2026-04-24T10:05:00Z"
  }
}
```

完整模型见 [doc/architecture.md](doc/architecture.md#5-timelinemanifest-数据模型)。

---

## 设计原则

- **清单为中心**：层间以 `timeline_manifest.json` 字段约定耦合。
- **Append-only 句面**：不原地改写 `annotations[].content`；消歧走 `comprehension` 侧字段。
- **index 主坐标**：L2 对齐句序号；时间在 L3 还原。
- **线性主链 + DAG 依赖**：6 节点、`reads`/`writes` 推导顺序。
- **事件驱动**：CLI / TUI 订阅 EventBus，与调度解耦。

---

## 文档导航

| 文档 | 内容 | 适合谁 |
|------|------|--------|
| [doc/architecture.md](doc/architecture.md) | 6 节点拓扑、manifest、代码结构 | 开发者 |
| [doc/intelligence.md](doc/intelligence.md) | L2 契约、LLM 流程、REFLOW | 改 L2 的开发者 |
| [doc/cli-and-config.md](doc/cli-and-config.md) | CLI、`--stage`、`config.toml` | 用户、运维 |
| [doc/decisions.md](doc/decisions.md) | ADR | 架构决策 |
| [doc/future-roadmap.md](doc/future-roadmap.md) | 未来方向 | 规划 |
| [doc/DOCUMENTATION-CHANGELOG.md](doc/DOCUMENTATION-CHANGELOG.md) | 文档与代码对齐的变更摘要 | 审阅者 |

---

## 测试

```bash
pytest
```

---

## 常见问题

| 现象 | 处理方向 |
|------|----------|
| ASR / ForcedAligner 目录不存在 | 按上文下载；检查 `config.toml` 与工作目录；或 `--asr-model` / `--forced-aligner` 绝对路径 |
| CUDA OOM | `--dtype bfloat16`、更小 ASR、或 `--backend vllm`（需环境支持） |
| L2 API 报错 | 检查 `[llm]`、网络、模型名 |
| `--stage 23` / `3` 报错 | 清单需非空 `annotations[]`；L3 另需每条含 `t_start`/`t_end` 与合法的 `current.keep_mask[]` |
| 2c 循环 | 调整 `two_c_max_review_rounds`；`0` 关闭真实审核 |
| 2d 回流无效 | 调整 `two_d_max_reflows`；`0` 禁用回流 |

---

## License

MIT
