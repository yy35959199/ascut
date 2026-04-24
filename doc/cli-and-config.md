# CLI 与配置参考

> 最后更新：2026-04-24

---

## 目录

1. [安装与依赖](#1-安装与依赖)
2. [命令行用法](#2-命令行用法)
3. [config.toml 配置项](#3-configtoml-配置项)
4. [典型使用场景](#4-典型使用场景)
5. [输出产物](#5-输出产物)

---

## 1. 安装与依赖

```bash
# 安装 AutoSmartCut（editable install）
pip install -e "ascut/"

# 安装 Qwen3-ASR（editable install，需要 vLLM extra）
pip install -e "./Qwen3-ASR[vllm]"

# smartcut 通过 PyPI 安装
pip install smartcut
```

**运行时依赖：**
- Python 3.11+
- CUDA GPU（ASR 推理）
- FFmpeg（通过 smartcut/PyAV 间接引入）
- Textual（TUI 模式，`pip install textual`）
- Silero VAD（L3 切点吸附，`pip install silero-vad`）

---

## 2. 命令行用法

### 2.1 `ascut run` — 单视频流水线

```
ascut run [--stage SPEC] [--input VIDEO | --manifest MANIFEST] [选项...]
```

### 2.2 `ascut tui` — TUI 交互模式

```
ascut tui [--stage SPEC] [--input VIDEO | --manifest MANIFEST] [选项...]
```

`ascut tui` 与 `ascut run` 接受完全相同的参数，区别在于启动 Textual TUI 界面，支持人工审阅交互。

### 2.3 公共参数表

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--stage SPEC` | string | `123` | 执行哪些阶段，见 §2.4 |
| `--from-stage N` | int | — | **已弃用**，映射为等价 `--stage`，见 §2.5 |
| `--input PATH` | Path | — | 输入视频文件（`--stage` 含 `1` 时必填） |
| `--manifest PATH` | Path | — | `timeline_manifest.json` 路径（`--stage` 不以 `1` 开头时必填） |
| `--goal TEXT` | string | `""` | 智能层目标，传给 LLM |
| `--output-dir PATH` | Path | 自动生成 | 产物目录；含 `1` 且省略时为 `<视频父目录>/ascut_out_<ULID前8位>` |
| `--output-name NAME` | string | — | 输出视频文件名（basename），落在 `output_dir` |
| `--interactive-2d` | flag | False | 启用 2d TUI 人工审阅；默认 auto 跳过（仅 `ascut run` 有效，`ascut tui` 始终启用） |
| `--two-b-mode MODE` | `single`\|`block` | config 值 | 覆盖 `config.toml` 的 `two_b_mode` |
| `--config PATH` | Path | `config.toml` | 指定配置文件路径 |
| `--asr-model PATH` | Path | config 值 | 覆盖 ASR 模型目录 |
| `--forced-aligner PATH` | Path | config 值 | 覆盖 ForcedAligner 目录 |
| `--backend MODE` | `transformers`\|`vllm` | config 值 | ASR 推理后端 |
| `--device DEVICE` | string | `cuda:0` | 推理设备 |
| `--dtype TYPE` | `float16`\|`bfloat16`\|`float32` | `float16` | 推理精度 |
| `--language LANG` | string | `Chinese` | ASR 语言 |
| `--gpu-memory-utilization RATIO` | float | `0.8` | vLLM GPU 内存利用率 |
| `--pre-pad SEC` | float | `0.15` | 每个保留段起点向前扩展（秒） |
| `--post-pad SEC` | float | `0.25` | 每个保留段终点向后扩展（秒） |
| `--min-duration SEC` | float | `1.0` | 过短段合并到相邻段的阈值（秒） |
| `--no-vad-snap` | flag | False | 关闭 L3 VAD 切点吸附 |
| `--verbose` | flag | False | 启用 DEBUG 日志 |

### 2.4 `--stage` 所有合法值

| 值 | 执行内容 | 输入要求 |
|----|---------|---------|
| `1` | 完整 L1（L1A + L1B） | `--input` |
| `2` | 仅智能层 | `--manifest`（需含 `annotations[]`） |
| `3` | 仅执行层 | `--manifest`（需含 `annotations[]` + `current.keep_mask`） |
| `12` | L1 + L2 | `--input` |
| `23` | L2 + L3 | `--manifest`（需含 `annotations[]`） |
| `123` | 全流程（默认） | `--input` |
| `1a` | 仅 L1A（ASR 文本定稿，无时间轴） | `--input` |
| `1b` | 仅 L1B（强制对齐补时间） | `--manifest`（需含 L1A 产出） |
| `1a2` | L1A + L2（L1B 并行，L3 需另跑） | `--input` |
| `1b2` | L1B + L2 | `--manifest`（需含 L1A 产出） |
| `1a23` | L1A + L2 + L3（注意：L3 需要时间轴，须确保 L1B 已完成） | `--input` |
| `1b23` | L1B + L2 + L3 | `--manifest`（需含 L1A 产出） |

**`--input` / `--manifest` 互斥规则：**
- `--stage` 含 `1` 或 `1a`（需从视频创建清单）：必须 `--input`，禁止 `--manifest`
- `--stage` 不以 `1`/`1a` 开头（续跑已有清单）：必须 `--manifest`，禁止 `--input`

### 2.5 `--from-stage` 兼容映射（已弃用）

| `--from-stage` | 等价 `--stage` |
|----------------|----------------|
| `1` | `123` |
| `2` | `23` |
| `3` | `3` |

`--stage` 与 `--from-stage` 不得同时指定。

---

## 3. config.toml 配置项

配置文件默认路径：`ascut/config.toml`。可通过 `--config PATH` 指定其他路径。

### 3.1 `[perception]` — 识别层

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `segmentation_mode` | `"punctuation"` | 句级切分模式：`"punctuation"`（按标点）或 `"timing"`（按停顿时长） |
| `split_pause_threshold` | `0.20` | timing 模式下的停顿阈值（秒），仅在 `timing` 模式下生效 |
| `silence_threshold` | `0.80` | 静音判定阈值（秒），向后兼容保留 |
| `max_chars` | `60` | 单句最大字符数，超过时强制切分 |
| `sentence_endings` | 见下 | 标点切分依据列表（punctuation 模式） |

`sentence_endings` 默认值包含常见中英文全/半角标点：`。！？；\n，,.!?;:：—…、（）()""'"《》`

### 3.2 `[intelligence]` — 智能层

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `two_b_mode` | `"single"` | 2b 决策模式：`"single"`（全文一次调用）或 `"block"`（按 outline_blocks 分块调用） |
| `two_b_block_size_limit` | `0` | block 模式下单块句数警告阈值（`0` = 不限制） |
| `two_c_max_review_rounds` | `1` | 2c 审核最大修正轮次（`0` = 占位透传不调 LLM，`1` = 审核+最多 1 轮修正） |
| `two_c_must_pass_rate` | `1.0` | 2c must 项通过率阈值（`1.0` = 全部 must 必须通过） |
| `two_d_max_reflows` | `3` | 2d 人工审阅最大回流次数（`0` = 禁用回流） |

**CLI 覆盖关系：**
- `--two-b-mode` 覆盖 `two_b_mode`

### 3.3 `[execution]` — 执行层

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `gap_after_cap` | `0.6` | 保留段右边界延伸上限（秒）：最后一句 `t_end` 后再纳入 `min(gap_after, gap_after_cap)` 秒 |
| `vad_snap_enabled` | `true` | L3 VAD 切点吸附总开关；CLI `--no-vad-snap` 时关闭 |
| `vad_snap_radius` | `0.12` | 入点/出点各自在 ±radius（秒）内搜索静音并吸附 |
| `vad_threshold` | `0.35` | Silero `get_speech_timestamps` 的 `threshold` |
| `vad_min_silence_ms` | `80` | Silero `min_silence_duration_ms` |
| `vad_speech_pad_ms` | `10` | Silero `speech_pad_ms` |
| `parallel_l1b_l2_enabled` | `true` | L1A 完成后 L1B 与 L2 是否并行（`--stage` 含 `1a*` 且含 `2` 时生效） |
| `sentence_tile_cache_enabled` | `true` | L3 是否尝试 seam_index + ffmpeg concat 快速成片（失败自动回退 smartcut） |

**CLI 覆盖关系：**
- `--no-vad-snap` 关闭 `vad_snap_enabled`
- `--pre-pad`、`--post-pad`、`--min-duration` 直接传入 L3，不对应 config.toml 项

### 3.4 `[models]` — 模型配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `asr_model_path` | `"models/Qwen3-ASR-1.7B"` | Qwen3-ASR 模型目录路径 |
| `forced_aligner_path` | `"models/Qwen3-ForcedAligner-0.6B"` | ForcedAligner 模型目录路径 |
| `backend` | `"transformers"` | ASR 推理后端：`"transformers"` 或 `"vllm"` |

**CLI 覆盖关系：**
- `--asr-model` 覆盖 `asr_model_path`
- `--forced-aligner` 覆盖 `forced_aligner_path`
- `--backend` 覆盖 `backend`

---

## 4. 典型使用场景

### 4.1 全流程处理

```bash
ascut run \
  --stage 123 \
  --input /path/to/video.mp4 \
  --goal "保留核心论点，删除重复和闲聊" \
  --output-dir /path/to/output
```

### 4.2 仅识别层（生成标注，不做智能决策）

```bash
ascut run \
  --stage 1 \
  --input /path/to/video.mp4 \
  --output-dir /path/to/output
```

### 4.3 续跑智能层（已有 L1 结果）

```bash
ascut run \
  --stage 2 \
  --manifest /path/to/output/timeline_manifest.json \
  --goal "保留技术讲解部分，删除广告"
```

### 4.4 带人工审阅的全流程（TUI 模式）

```bash
ascut tui \
  --stage 123 \
  --input /path/to/video.mp4 \
  --goal "精华版，压缩至原时长 60%"
```

### 4.5 L1A 后并行跑 L2，再单独跑 L1B 和 L3

```bash
# 步骤 1：L1A + L2 并行（L1B 在后台并行执行）
ascut run \
  --stage 1a2 \
  --input /path/to/video.mp4 \
  --goal "..."

# 步骤 2：L1B（补时间轴，若步骤 1 中 L1B 未完成）
ascut run \
  --stage 1b \
  --manifest /path/to/output/timeline_manifest.json

# 步骤 3：L3（执行剪切）
ascut run \
  --stage 3 \
  --manifest /path/to/output/timeline_manifest.json
```

---

## 5. 输出产物

### 5.1 `timeline_manifest.json` 位置

- 含 `--stage 1` 或 `1a`（新建清单）：创建在 `output_dir/timeline_manifest.json`
- 续跑（`--manifest` 指定已有清单）：原地更新同一文件

### 5.2 输出视频位置

- 默认：`output_dir/<原视频名>_cut<原扩展名>`（如 `interview_cut.mp4`）
- 可通过 `--output-name` 指定文件名

### 5.3 续跑与分叉规则

**续跑**：`--manifest` 指定已有清单，且未指定 `--output-dir` 或 `--output-dir` 与 manifest 所在目录相同 → 原地更新同一文件，`run_id` 不变。

**分叉**：`--output-dir` 指向另一目录 → 仅拷贝 `timeline_manifest.json` 到新目录，分配新 `run_id` 后再写入；源视频路径仍指向原媒体文件（不复制视频本体）。

### 5.4 `output_dir` 自动命名规则

含 `--stage 1` 且未指定 `--output-dir` 时，自动创建：
```
<视频父目录>/ascut_out_<ULID前8位>/
```
例如：`/videos/ascut_out_01KNSX8E/`
