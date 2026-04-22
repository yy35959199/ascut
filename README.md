# AutoSmartCut

> 时间轴媒体语义处理管道：把原始视频编译为可持久化的**时间轴清单**（`timeline_manifest.json`），在清单上完成理解与逐句保留决策，再帧精确出片。

## 分发说明（请先读）

- 本仓库**默认不包含** `models/` 目录，**不提供**预下载的 Qwen 语音权重；首次使用识别层（L1）前，你必须自行从 **Hugging Face**（或 ModelScope / 离线拷贝等等价方式）拉取模型到本地，并在 `config.toml` 或 CLI 中指向正确路径。
- 本 README **自成体系**：安装、下模型、配置与行为边界均以本文与源码为准；不假定存在任何额外说明目录。
- 层间主文件为 **`timeline_manifest.json`**（与 `autosmartcut.manifest_io.MANIFEST_FILENAME` 一致），清单内 `version` 常见为 **`1.0-mini`**。

---

## 核心思想

视频文件是输入，不是核心资产。**时间轴清单**才是。

系统将处理建模为「编译」：识别层把媒体变成句级 `annotations[]`；智能层在 **index** 主坐标上产出 `comprehension` 与 `keep_mask`；执行层把 `keep_mask` 还原为时间区间并调用 **smartcut** 做 GOP 级剪切。Layer1 写入的 `annotations[].content` **不被原地改写**；纠错由 LLM 输出坐标与意图，**程序**生成稠密消歧文本供 L2 使用（见下文「落盘形态」）。

---

## 架构总览

```text
原始视频 → [识别层 L1] → annotations[] → [智能层 L2] → keep_mask → [执行层 L3] → 成片
                              ↑                                            ↑
                     timeline_manifest.json（层间唯一主文件）
```

| 层 | 职责 | 核心产出 |
|----|------|---------|
| **L1 识别** | 解码音频 → ASR →（可选拆分 **L1A** 仅文本 / **L1B** 仅对齐补时）→ 句级聚合 | `annotations[]`（`gap_after`；字级时间戳是否落盘以当前 `compact_annotations` 为准） |
| **L2 智能** | 2a 理解 → 2b 决策 → 2c 占位 → 2d 可选人工 | `current.comprehension`、`current.keep_mask`（与句条数等长） |
| **L3 执行** | mask + 时间轴 → 保留区间 → 可选 VAD 吸附 → smartcut | 成片视频 |

**智能层（最小认知）**

- **2a**：同一多轮会话上 **两次**结构化 LLM 调用（R1、R2），随后由**程序**根据 R2 的 `corrections` 等生成稠密 `cleaned_annotations`（**仅内存**，供 2b）。
- **2b**：**一次**结构化 LLM 调用，输出与 `annotations[]` 等长的 `keep_mask`。
- **2c**：当前版本为自动 **pass**，不调 LLM。
- **2d**：默认跳过；加 `--interactive-2d` 可在 CLI 逐条修改 `keep` 后写回清单。
- **LLM 调用次数（稳定路径）**：**3 次**（2a×2 + 2b×1）。

保存清单前会执行 **`strip_volatile_fields`**（`autosmartcut/manifest_io.py`）：移除 **`tokens`**、**`cleaned_annotations`**、**`l2_checkpoints`** 等不应长期落盘的键。

层间只通过清单字段约定耦合，各层实现可替换。

---

## 能力边界与路线图（以本仓库代码为准）

下列使用 Markdown 任务列表：`- [x]` 表示**当前版本已具备**；`- [ ]` 表示**尚未提供或仍在规划中**。长期方向不依赖任何外部文档。

### 管道与编排

**当前版本**

- [x] `ascut run`，`--stage` 为 `1` / `2` / `3` / `12` / `23` / `123`，以及 **`1a` / `1b` / `1a2` / `1b2` / `1a23` / `1b23`**（均未指定时默认 `123`）
- [x] `--stage` 为完整 L1 或 **`1a*`** 时使用 `--input`；否则（续跑清单，含 **`1b*`**）使用 `--manifest`
- [x] `--from-stage` 仅作为 `--stage` 的兼容别名（仍建议使用 `--stage`）

**长期方向**

- [ ] 检查点目录与断点续跑（如 `--resume`）
- [ ] 插件式节点、多成片预设、批量目录处理

### 清单与数据

**当前版本**

- [x] 层间以单一 `timeline_manifest.json` 为主文件
- [x] 保存前剥离仅运行时字段（如 `tokens`、`cleaned_annotations`、`l2_checkpoints` 等，见 `manifest_io.strip_volatile_fields`）
- [x] L3 使用 `annotations[]` 的时间信息与 `current.keep_mask` 合成剪切区间，不要求清单内落盘 EDL

**与本仓库一致的约定**（无单独文档时仍成立）

- [x] `tokens[]` 由 `annotations[]` 在内存派生，不落盘
- [x] 稠密 `cleaned_annotations` 供 L2 运行时消费，正式落盘前移除
- [x] 正式流程不依赖历史教具文件名（如 `layer1_annotations.json` 等）

**长期方向**

- [ ] 清单内显式 `edl[]` 与多子系统共享
- [ ] 多轮次快照（如 `current` / `previous`）与历史裁剪写入同一文件

### 智能层

**当前版本**

- [x] 2a：两轮结构化 LLM + 程序生成稠密消歧供 2b
- [x] 2b：一轮结构化 LLM，输出与句条数等长的 `keep_mask`
- [x] 2c：占位自动通过（不调 LLM）
- [x] 2d：默认跳过；`--interactive-2d` 可 CLI 人工改 mask

**长期方向**

- [ ] checklist 主流程与覆盖报告
- [ ] 真实 2c LLM 审核与回路控制
- [ ] 多轮次、Token 预算、结构化自然语言反馈回流

### 识别与执行

**当前版本**

- [x] L1：Qwen3-ASR + Qwen3-ForcedAligner + 句级聚合
- [x] L3：smartcut；可选 Silero VAD 切点吸附（`--no-vad-snap` 关闭）

**长期方向**

- [ ] 更多识别通道（情绪、说话人等）
- [ ] 字幕导出、GUI、声纹分离等

### 模型与 L1 权重

**当前版本**

- [x] 本地目录 **`asr_model_path`** 与 **`forced_aligner_path`** 须存在且可被 `Qwen3ASRModel` 加载（见 `perception.py`）
- [x] CLI 可用 **`--asr-model`**、**`--forced-aligner`** 覆盖配置文件中的路径

**与本仓库一致的约定**

- [x] `config.toml` 中路径相对**进程当前工作目录**解析；建议在项目根执行命令或改用绝对路径

---

## 技术选型

| 环节 | 选型 | 说明 |
|------|------|------|
| 语音转写 | Qwen3-ASR（默认 1.7B） | 中文转写基线 |
| 字级对齐 | Qwen3-ForcedAligner-0.6B | 字级时间戳，服务精确切点 |
| 语义决策 | DeepSeek（OpenAI 兼容 HTTP API） | L2 理解与决策 |
| 视频剪切 | smartcut | GOP 级 Remux，非切点区域零重编码损失 |
| 音频 | PyAV | 解码与 WAV 准备 |
| 切点吸附 | Silero VAD (ONNX) | 可选，改善接缝听感 |

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

**PowerShell**

```powershell
New-Item -ItemType Directory -Force -Path models | Out-Null
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

其余 `[perception]`、`[execution]`、`[intelligence]` 节有默认值；进阶调参可编辑 `config.toml` 或阅读源码注释。

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

**启用 2d 人工逐条审阅**：

```bash
ascut run --input video.mp4 --goal "..." --stage 123 --interactive-2d
```

**显式指定 ASR 权重目录**（覆盖配置文件）：

```bash
ascut run --input video.mp4 --stage 1 --asr-model models\Qwen3-ASR-1.7B --forced-aligner models\Qwen3-ForcedAligner-0.6B
```

首次含 L1 且未指定 `--output-dir` 时，默认输出目录为**视频同目录下的 `ascut_out_<ULID 前 8 位>`**；清单为 **`{output_dir}\timeline_manifest.json`**（以 `PipelineRun` 实现为准）。

---

## CLI 参数速查

| 参数 | 说明 |
|------|------|
| `--stage SPEC` | `1` / `2` / `3` / `12` / `23` / `123`；省略且未使用 `--from-stage` 时默认 `123` |
| `--from-stage` | **已弃用**：映射为等价 `--stage`（1→123，2→23，3→3） |
| `--input` | 输入视频；**`--stage` 含 `1` 时必填** |
| `--manifest` | 已有 `timeline_manifest.json`；**`--stage` 不含 `1` 时必填** |
| `--goal` | 智能层目标（L2） |
| `--output-dir` | 产物目录 |
| `--output-name` | 输出视频文件名（basename） |
| `--interactive-2d` | 启用 2d CLI 人工审阅 |
| `--two-b-mode` | `single` 或 `chunked` |
| `--no-vad-snap` | 关闭 L3 VAD 切点吸附 |
| `--config` | 指定 `config.toml` |
| `--asr-model` / `--forced-aligner` | 覆盖 `[models]` 路径 |
| `--backend` | `transformers` 或 `vllm` |
| `--device` | 如 `cuda:0` |
| `--dtype` | `float16` / `bfloat16` / `float32` |
| `--language` | 默认可为 `Chinese` |
| `--gpu-memory-utilization` | vLLM 等后端显存占用上限 |
| `--pre-pad` / `--post-pad` / `--min-duration` | L3 区间与合并参数 |
| `--verbose` | DEBUG 日志 |

完整列表以 **`ascut run --help`** 为准。

---

## 项目结构（节选）

```text
ascut/
├── autosmartcut/
│   ├── runner.py              # CLI：ascut run
│   ├── pipeline_run.py      # run_id、路径
│   ├── manifest_io.py       # 清单读写、strip_volatile_fields
│   ├── manifest_stages.py   # --stage / --from-stage
│   ├── manifest.py          # dataclass 草图
│   ├── annotation_tokens.py
│   ├── config.py
│   ├── perception.py        # L1
│   ├── intelligence.py      # L2 编排
│   ├── intelligence_2a.py
│   ├── intelligence_2b.py
│   ├── intelligence_2c.py
│   ├── intelligence_2d.py
│   ├── intelligence_llm.py
│   ├── execution.py         # L3
│   ├── timeline_segments.py
│   └── vad_silence.py
├── demos/
├── tests/
├── config.toml
└── pyproject.toml
```

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
      "metadata": { "char_timestamps": [] }
    }
  ],
  "current": {
    "comprehension": {
      "purpose": "...",
      "outline_blocks": [],
      "corrections": []
    },
    "keep_mask": [
      { "index": 0, "keep": true },
      { "index": 1, "keep": false }
    ]
  },
  "layer_status": {}
}
```

**说明**：运行时 `comprehension` 内可能含程序生成的稠密 `cleaned_annotations`，保存前会被移除。字段与校验以当前版本 **`autosmartcut/intelligence_*.py`** 与 **`tests/`** 为准；本 README 不重复 JSON Schema 全文。

---

## 设计原则（工程约定）

- **清单为中心**：层间通过 `timeline_manifest.json` 字段约定耦合。
- **Append-only**：不原地改写 L1 的 `annotations[].content`。
- **开放 `metadata`**：便于字级时间戳等扩展。
- **index 主坐标**：L2 决策对齐句级序号；时间还原在 L3。
- **消歧确定性**：LLM 输出纠错意图，程序生成稠密消歧视图供 2b 使用。

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

---

## License

MIT
