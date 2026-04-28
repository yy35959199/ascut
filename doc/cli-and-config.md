# CLI 与配置参考

> 最后更新：2026-04-28

---

## 目录

1. [安装与依赖](#1-安装与依赖)
2. [命令行用法](#2-命令行用法)（含 `ascut resume` §2.7）
3. [config.toml 配置项](#3-configtoml-配置项)
4. [典型使用场景](#4-典型使用场景)
5. [输出产物](#5-输出产物)

---

## 1. 安装与依赖

在项目根目录（含 `pyproject.toml`）执行：

```bash
pip install -e .
```

开发依赖（运行测试）：

```bash
pip install -e ".[dev]"
```

**运行时依赖（摘要）：**

- Python ≥ 3.11（见 `pyproject.toml`）
- NVIDIA GPU + CUDA（L1 ASR / 对齐）
- 依赖包由 `pip install -e .` 安装，主要包括：`torch`、`av`、`textual`、`openai`、`qwen-asr`、`silero-vad` 等（以 `pyproject.toml` 为准）
- FFmpeg：经 PyAV / smartcut 链路使用
- 本地 Qwen ASR 与 ForcedAligner 权重需自行下载（见 [README.md](../README.md)）

---

## 2. 命令行用法

### 2.1 子命令

| 子命令 | 说明 |
|--------|------|
| `ascut run` | 非交互流水线（默认 2d 自动确认） |
| `ascut tui` | Textual 交互界面；**可**使用位置参数 `path` 打开媒体或清单 |
| `ascut resume` | 读取 `timeline_manifest.json`（或其父目录）推断进度，确认后以等价 `ascut run` / `ascut tui` 续跑；`--stage` 覆盖值须为 §2.5 六值之一（见 §2.7） |

### 2.2 `ascut run`

```
ascut run [--stage SPEC] (--input VIDEO | --manifest MANIFEST) [选项...]
```

### 2.3 `ascut tui`

**推荐：**

```
ascut tui [path]
```

`path` 为可选：媒体文件（新建工程）或 `timeline_manifest.json` / 其父目录（续跑）。

**兼容旧用法**（与 `run` 相同的 `--stage`、`--input`、`--manifest` 等流水线参数）：

```
ascut tui [--stage SPEC] (--input VIDEO | --manifest MANIFEST) [选项...]
```

> `ascut run` 与 `ascut tui` **共享大部分流水线参数**；差异在于：`ascut tui` 支持上述位置参数 `path`，且 **始终**进入交互审阅语义；`--interactive-2d` **仅对 `run` 有意义**（`run` 默认自动确认 2d）。

### 2.4 公共参数表（`run` / `tui` 共用部分）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--stage SPEC` | string | 省略且未用 `--from-stage` 时等价 `123` | 仅允许 §2.5 中的值 |
| `--from-stage N` | int | — | **已弃用**；映射见 §2.6 |
| `--input PATH` | Path | — | `--stage` **含 `1`** 时必填 |
| `--manifest PATH` | Path | — | `--stage` **不含 `1`** 时必填 |
| `--goal TEXT` | string | `""` | L2 剪辑意图 |
| `--output-dir PATH` | Path | 自动生成 | 含 L1 且省略时见 §5.4 |
| `--output-name NAME` | string | — | 输出视频 basename |
| `--interactive-2d` | flag | False | **仅** `ascut run`：排队 2d 人工输入 |
| `--two-b-mode` | `single` \| `block` | 来自 config | 覆盖 `[intelligence].two_b_mode` |
| `--config PATH` | Path | 仓库根 `config.toml` | 见 `autosmartcut/config.py` |
| `--asr-model` | Path | config | 覆盖 `asr_model_path` |
| `--forced-aligner` | Path | config | 覆盖 `forced_aligner_path` |
| `--backend` | `transformers` \| `vllm` | config | 覆盖 `[models].backend` |
| `--device` | string | `cuda:0` | 推理设备 |
| `--dtype` | `float16` \| `bfloat16` \| `float32` | `float16` | 精度 |
| `--language` | string | `Chinese` | ASR 语言 |
| `--gpu-memory-utilization` | float | `0.8` | vLLM 相关 |
| `--pre-pad` | float | `0.15` | L3 段前 padding（秒） |
| `--post-pad` | float | `0.25` | L3 段后 padding（秒） |
| `--min-duration` | float | `1.0` | L3 过短段合并阈值 |
| `--no-vad-snap` | flag | False | 关闭 L3 VAD 吸附 |
| `--verbose` | flag | False | DEBUG 日志 |

### 2.5 `--stage` 合法值（全部）

| 值 | 执行内容 | 输入 |
|----|----------|------|
| `1` | 仅阶段 1：识别（`l1_perception`） | `--input` |
| `2` | 仅阶段 2：智能（2a→2b↔2c→2d） | `--manifest` |
| `3` | 仅阶段 3：执行（`l3_execute`） | `--manifest` |
| `12` | 阶段 1 + 2 | `--input` |
| `23` | 阶段 2 + 3 | `--manifest` |
| `123` | 全流程（默认） | `--input` |

**`--input` / `--manifest` 规则：**

- `--stage` **含 `1`**：必须 `--input`，不得 `--manifest`（本次运行创建/写入清单目录）。
- `--stage` **不含 `1`**：必须 `--manifest` 指向已有 `timeline_manifest.json`，不得 `--input`。

### 2.6 `--from-stage`（已弃用）

| `--from-stage` | 等价 `--stage` |
|----------------|----------------|
| `1` | `123` |
| `2` | `23` |
| `3` | `3` |

与 `--stage` 不得同时使用。

### 2.7 `ascut resume`

```
ascut resume <path> [--goal TEXT] [--stage SPEC] [--tui] [--yes|-y] [选项...]
```

- **`path`**（位置参数，必填）：`timeline_manifest.json` 或其**父目录**（不接受纯媒体文件路径）。
- **`--stage`**：可选；覆盖自动推断的下一阶段。取值**必须**为 §2.5 所列 **`1` / `2` / `3` / `12` / `23` / `123`** 之一（续跑最终会构造 `ascut run` / `ascut tui` 子命令，受同一白名单校验）。
- **`--goal`**：续跑 L2 时若清单无 `goal` 则必填；否则用于覆盖或补充意图。
- **`--tui`**：以 TUI 方式执行后续流水线（等价把预览命令里的 `ascut run` 换成 `ascut tui`）。
- **`-y` / `--yes`**：跳过「继续？」确认。
- 与 `run` 共用的产物相关项：`--output-dir`、`--output-name`、`--config`、`--no-vad-snap`、`--verbose`。

> **说明**：若 `ascut resume -h` 中 `--stage` 的 help 仍出现历史占位字样，**以本文档与** `PipelineSession.parse_stage_arg()` **为准**。

---

## 3. config.toml 配置项

默认加载路径：**仓库根目录** `config.toml`（与包目录同级）。可通过 `--config` 覆盖。

### 3.1 `[perception]`

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `segmentation_mode` | `"punctuation"` | `"punctuation"` \| `"timing"` |
| `split_pause_threshold` | `0.20` | 仅 `timing` 生效 |
| `silence_threshold` | `0.80` | 兼容保留 |
| `max_chars` | `60` | 单句最大字符 |
| `sentence_endings` | 内置列表 | 标点切分 |

### 3.2 `[intelligence]`

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `two_b_mode` | `"single"` | 2b：`single` \| `block` |
| `two_b_block_size_limit` | `0` | block 块过大告警阈值，`0` 关闭 |
| `two_c_max_review_rounds` | `1` | `0` 关闭真实 2c LLM |
| `two_c_must_pass_rate` | `1.0` | must 项通过率阈值 |
| `two_d_max_reflows` | `3` | 2d REFLOW 上限，`0` 禁用 |

CLI：`--two-b-mode` 覆盖 `two_b_mode`。

### 3.3 `[execution]`

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `gap_after_cap` | `0.6` | 句末向右延伸上限（秒） |
| `vad_snap_enabled` | `true` | VAD 总开关 |
| `vad_snap_radius` | `0.12` | 吸附半径（秒） |
| `vad_threshold` | `0.35` | Silero 阈值 |
| `vad_min_silence_ms` | `80` | 最小静音 |
| `vad_speech_pad_ms` | `10` | 语音垫音 |

CLI：`--no-vad-snap` 关闭 `vad_snap_enabled`。`--pre-pad` / `--post-pad` / `--min-duration` 仅 CLI → L3，无对应 config 键。

### 3.4 `[models]`

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `asr_model_path` | `models/Qwen3-ASR-1.7B` | ASR 目录 |
| `forced_aligner_path` | `models/Qwen3-ForcedAligner-0.6B` | 对齐模型目录 |
| `backend` | `transformers` | `transformers` \| `vllm` |

CLI：`--asr-model`、`--forced-aligner`、`--backend` 覆盖。

### 3.5 `[llm]`

由 `intelligence_llm` 读取（字段名以代码为准）：至少需 `api_key`、`base_url`、`model` 等。勿将密钥提交到版本库。

---

## 4. 典型使用场景

### 4.1 全流程

```bash
ascut run --stage 123 --input path\to\video.mp4 --goal "保留核心，删口误"
```

### 4.2 仅识别

```bash
ascut run --stage 1 --input path\to\video.mp4
```

### 4.3 仅智能（已有清单）

```bash
ascut run --stage 2 --manifest path\to\timeline_manifest.json --goal "你的意图"
```

### 4.4 仅执行（需已有 `annotations` 与 `current.keep_mask`）

```bash
ascut run --stage 3 --manifest path\to\timeline_manifest.json --pre-pad 0.2 --post-pad 0.3
```

### 4.5 TUI 全流程

```bash
ascut tui path\to\video.mp4
```

或：

```bash
ascut tui --stage 123 --input path\to\video.mp4 --goal "精华版"
```

---

## 5. 输出产物

### 5.1 清单路径

- **新建**（`--stage` 含 `1`）：`{output_dir}/timeline_manifest.json`，`output_dir` 默认见 §5.4。
- **续跑**（`--manifest`）：默认**原地更新**该文件（`run_id` 不变，除非走 fork）。

### 5.2 输出视频

- 默认：`{output_dir}/{原视频 stem}_cut{后缀}`
- `--output-name` 指定 basename。

### 5.3 续跑与分叉

- **续跑**：`--manifest` 指向已有文件；`--output-dir` 缺省或与清单同目录 → 原地更新。
- **分叉**：`--output-dir` 为另一已存在目录 → `PipelineRun.fork` 拷贝清单至新目录并分配**新** `run_id`（不复制视频文件）。

### 5.4 默认输出目录

含 `--stage` 的 **`1`** 且未指定 `--output-dir` 时：

```
{视频父目录}\ascut_out_{YYYY-mm-DD_HH-MM-ss.SSS}\
```

冲突时追加 `_01` … `_99`。

### 5.5 日志

同目录下 `run_{YYYY-mm-DD_HH-MM-ss.SSS}.log`，冲突时 `_01` 后缀。清单内 `run_id` 仍为 ULID，与日志文件名无关。
