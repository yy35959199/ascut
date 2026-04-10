# AutoSmartCut — MVP 落地规划文档

> 本文档是 [AutoSmartCut.md](AutoSmartCut.md) 的配套落地规划。
> AutoSmartCut.md 描述架构愿景与长期可能性；本文档聚焦于 **MVP 的具体实现设计、技术决策记录、Demo 验证方案**，是开发阶段的直接参考基准。

---

## 目录

1. [MVP 总览](#1-mvp-总览)
2. [技术决策记录](#2-技术决策记录)
3. [MVP 数据模型](#3-mvp-数据模型)
4. [Demo 验证方案](#4-demo-验证方案)
5. [仓库结构](#5-仓库结构)
6. [各阶段实现细节](#6-各阶段实现细节)
7. [CLI 命令设计](#7-cli-命令设计)
8. [依赖清单](#8-依赖清单)
9. [MVP 之后的第一批扩展](#9-mvp-之后的第一批扩展)

---

## 1. MVP 总览

### 定位

MVP 是三层语义管道的**最简可运行实现**：每层各有硬编码节点，不实现插件注册系统，不追求功能完整性，目标是**让全链路跑通并验证核心假设**。

**三层节点配置（MVP）：**

| 层 | 节点 | 产出 |
|----|------|------|
| Layer 1 识别层 | Qwen3-ASR-1.7B 转写 + Qwen3-ForcedAligner-0.6B 字级对齐 + 句级聚合 + 每条 `gap_after`（句间间隔） | 句级标注列表（Annotations） |
| Layer 2 智能层 | DeepSeek LLM（2a：两轮调用 + 程序替换；2b：一次调用）+ 2c auto-pass + 2d CLI 人工审阅 | **`comprehension`**（主旨 + `outline_blocks` + `cleaned_annotations`）+ 顶层 **`keep_mask`**（JSON3）；**不**在智能层落盘 EDL（由执行层内部合成），详见 [intelligence-layer2-mvp.md](intelligence-layer2-mvp.md) §11 |
| Layer 3 执行层 | `keep_mask`+JSON1 → 时间区间（含可选 **Silero VAD 切点吸附**）→ **smartcut**（帧精确剪切） | 最终视频文件 |

### 全链路 MVP（runner）与现行串接方式

- **全链路编排（现行）**：`runner.py` + **`ascut run`** / `python -m autosmartcut.runner run`，`--from-stage {1,2,3}`；L2 经 **`--layer2-json`** 读 JSON2，L3 读 JSON1+JSON3。
- **断点续跑（规划）**：配合 **`checkpoint.py`** 与视频同目录下的 **`.autosmartcut_<视频主文件名>/`**（`manifest.json` 等）及 **`--resume`** —— **尚未实现**。
- **现行可用路径（手工串联）**：
  1. Layer 1：`demos/demo1_asr.py`（或等价流程）产出 **JSON1**（`layer1_annotations.json`）；
  2. Layer 2：`python -m autosmartcut.intelligence <layer2_input.json> <layer2_output.json> [--goal "..."] [--auto] [--two-b-mode single|chunked]` 读 **JSON2**、产出 **JSON3**（仅含 `keep_mask`，见下文）；等价可运行 `python demos/demo2_llm.py --layer2 ... --output ...`；
  3. Layer 3：`demos/demo3_smartcut.py json ...` 或直接在代码中调用 `autosmartcut.execution`。

### Layer 2 在 MVP 中的简化

智能层在完整架构中包含四个子阶段（2a 理解→2b 决策→2c 审核→2d 人工）和双层循环。MVP 阶段做如下简化：

| 子阶段 | 完整架构 | MVP 实现 |
|--------|----------|----------|
| 2a 理解 | 完整版曾含符号表/检查清单等 | **MVP**：R1 单轮 LLM 产出粗主旨 + `outline_blocks_rough` + `candidate_misrecognitions`（仅内存）；R2 与 R1 **同一对话前缀上的第二跳**（`intelligence_llm` 多轮 API）产出精化主旨 + `outline_blocks` + `corrections`（仅内存）；**程序**按 `corrections` 生成持久化 `cleaned_annotations`；**不**持久化符号表；**不**启用 checklist 主流程（见 [intelligence-layer2-mvp.md](intelligence-layer2-mvp.md) §5） |
| 2b 决策 | 独立一次 LLM 调用，读 `cleaned_annotations` + 主旨/分块 | **与 index 级 keep 一致**；MVP 共计 **3 次 LLM 调用**（2a×2 + 2b×1） |
| 2c 审核 | LLM 对照检查清单做结构化审核，输出 pass / fix_decision / fix_checklist | **代码层自动 pass**：直接写入一条 `verdict="pass"` 的 `ReviewReport` |
| 2d 人工 | CLI 交互 | **MVP 现行**（见 [intelligence-layer2-mvp.md](intelligence-layer2-mvp.md) §8）：审阅 / **按 index 手动切换** / 确认；**无**自然语言反馈 `[r]` 与多轮回流 |
| 循环 | max_inner / max_outer 按 Token 预算动态分配 | **完全禁用**（max_inner=0, max_outer=0），人工兜底 |

这意味着 MVP 的 `loop_metadata` 每次都是 `inner_rounds=0, outer_rounds=0, final_verdict="pass"`，字段存在只为保证 schema 与完整版兼容。

### MVP 边界

**已实现（代码层）：**
- Layer 1：`perception.py` + `demo1_asr.py`（Qwen3-ASR + 对齐 + 句级聚合 + `gap_after`）
- Layer 2：`intelligence_2a` / `intelligence_2b` / `intelligence_2c`（占位 pass）/ `intelligence_2d`；运行时 **`manifest_dict`（dict）** 贯通 2a–2d，见 `intelligence.py`
- Layer 3：`execution.py`（JSON1+JSON3 → 区间 → smartcut）+ `demo3_smartcut.py`
- 2d：手动切换 index 的 keep/cut、确认后定稿 **`keep_mask`** 写入 JSON3（**不**在智能层落盘 EDL）

**已实现（编排）：**
- `runner.py`：**`ascut run`** / `python -m autosmartcut.runner run`；`--from-stage {1,2,3}`；L2 **必须**通过 **`--layer2-json`（JSON2）** 提供句面；L3 仍消费 JSON1+JSON3。

**规划中、尚未落地（仍属 MVP 文档目标，未删需求）：**
- `checkpoint.py` 与 **`.autosmartcut_<视频名>/`** 检查点目录及 `--resume` 等
- 单一 `manifest.json` 与 `TimelineManifest` dataclass 的**运行时**绑定（当前 Layer2 以 dict 为准，`manifest.py` 为类型/愿景草图）

**不包含（MVP 后扩展）：**
- 字幕文件生成
- 声纹识别 / 说话人分离
- GUI 界面
- 批量文件处理
- 插件注册系统
- 多剪辑策略预设
- 2c 真实 LLM 审核与双层循环

### 与 AutoSmartCut.md 的关系

```
AutoSmartCut.md     ← 架构愿景：定义了什么是可扩展的语义管道，射程是所有时间轴媒体
AutoSmartCut-MVP.md ← 落地规划：在架构愿景下，用最少代码验证最核心的价值假设
```

MVP 是愿景文档中"先把最简节点跑通"这条建议的具体执行方案。

---

## 2. 技术决策记录

以下 16 项决策均已确认，作为后续开发的基准约束。

| # | 决策项 | 选择 | 理由 |
|---|--------|------|------|
| D1 | 仓库结构 | 新建独立仓库 `AutoSmartCut` | 与 smartcut 职责完全不同，smartcut 作为 pip 依赖引入 |
| D2 | Demo 脚本存放 | 仓库内 `demos/` 目录，保留 `demo1_asr.py`、`demo2_llm.py`、`demo3_smartcut.py` 三个脚本 | 便于对照验证结果，提交到仓库作为验证记录 |
| D3 | ASR 引擎选型 | Qwen3-ASR-1.7B（转写）+ Qwen3-ForcedAligner-0.6B（字级对齐） | 字级对齐是精确切点的生死线（~0.1s 精度）；inference backend 选 vLLM，editable install（`pip install -e "./Qwen3-ASR[vllm]"`）；`Qwen3ASRModel.transcribe(return_time_stamps=True)` 一次调用同时返回转写文本与 `ForcedAlignResult`，perception 层在组装 `Annotation.metadata.char_timestamps` 时将 `ForcedAlignItem` 的 `start_time`/`end_time` 字段归一化为 `start`/`end`（更短，LLM Prompt 展示更紧凑） |
| D4 | 目标语言 | 中文为主（Demo 阶段） | 目标用户场景以中文内容创作为主 |
| D5 | 句间间隔 | 由相邻句级片段时间边界计算：`gap_after = 下一句 t_start − 当前句 t_end`；末句为 `媒体时长 − 当前句 t_end`。不引入 Silero VAD | 字级对齐提供句级 `t_start`/`t_end`；间隔写入每条标注的 `gap_after`，不单独插入静音行 |
| D6 | 首选 LLM | DeepSeek（V3/R1，OpenAI 兼容协议） | 中文理解好，成本极低，API 格式与 OpenAI 兼容，一个适配器覆盖大多数提供商 |
| D7 | LLM 分析目标传入 | CLI 参数 `--goal "..."` | LLM 需要明确目标才能做有意义的相关性评分；目标因场景差异大，由用户指定 |
| D8 | CLI 界面语言 | 中文 | 目标用户为中文使用者 |
| D9 | 人工反馈历史策略 | 保留所有轮次，超过 N 轮时压缩为摘要；N 可配置，Demo 阶段 N=1 | 累积上下文使 LLM 越来越了解用户意图；压缩机制控制 Prompt 长度 |
| D10 | smartcut 依赖方式 | `pip install smartcut`（PyPI 1.7） | 稳定版本；PyAV 通过 smartcut 间接引入，统一 FFmpeg 集成方式 |
| D11 | 检查点存储位置 | 与输入视频同目录，子目录名携带视频名（如 `.autosmartcut_video_name/`） | 便于关联，不污染其他目录；携带视频名避免多文件冲突 |
| D12 | 批量处理 | MVP 只支持单文件 | 降低 MVP 复杂度；单文件场景已足够验证核心流程 |
| D13 | 智能层 LLM 调用策略 | 2a **两次 LLM**（R1 单轮；R2 与 R1 **同一 `messages` 前缀上的第二跳**，见 `intelligence_llm.prepare_next_turn_messages` + `call_turn_structured`）+ **程序一步**生成 `cleaned_annotations`；2b **单轮** `call_llm_structured`；MVP 共计 **3 次 LLM 调用**；2c 仍为 auto-pass | 2a 中间结构不落盘；持久化 `comprehension` 仅 `purpose` / `outline_blocks` / `cleaned_annotations`；2b 读 `comprehension` + 内存 **`tokens[]`**（与 JSON2 一致）；与 [intelligence-layer2-mvp.md](intelligence-layer2-mvp.md) §4–§6、§5.1.1 一致 |
| D14 | 2c 审核子阶段 | MVP 中代码层自动 pass，不调用 LLM | 循环禁用时 2c 无实质作用；保留 `ReviewReport` 字段结构与完整版兼容；人工在 2d 承担审核兜底 |
| D15 | Qwen3-ASR 安装方式 | editable install：`pip install -e "./Qwen3-ASR[vllm]"`；不使用预打包版本 | Qwen3-ASR 仓库作为子目录存在于工作区，editable install 保证本地修改立即生效；选 vLLM extra 以启用高性能 inference backend |
| D16 | 句级聚合分割规则 | 分割模式：`punctuation`（默认）或 `timing`；punctuation 以句终标点为分割依据；timing 以 `split_pause_threshold` 为分割依据；`max_chars` 兜底；每条句级标注带 `gap_after` | `split_pause_threshold` 仅影响 timing 切分；配置项 `silence_threshold` 保留兼容，当前实现不用于插入静音行 |
| D17 | LLM 决策粒度 | LLM 通过 `keep_mask` 对**每条句级标注**输出 `keep: true/false`，不输出带时间戳的 Segment | 时间由 JSON1 的 `t_start`/`t_end`/`gap_after` 与 Layer 3 合并；`keep_mask` 与 **JSON2 `tokens[]`** 等长（全链一致时与 JSON1 `annotations[]` 条数相同），项项为布尔 |

### 关键决策说明：为何用 smartcut 而非 FFmpeg CLI

MVP 的 Layer 3（执行层）直接调用 smartcut 库，而非自行拼接 FFmpeg `filter_complex` 命令字符串。原因：

| 维度 | FFmpeg CLI 方案 | smartcut 库方案 |
|------|----------------|----------------|
| 切点精度 | 关键帧对齐（不精确）或全片重编码（极慢） | GOP 级 Remux + 切点局部 Recode，帧精确 |
| 速度 | 全片重编码与视频时长成正比 | 大部分 GOP Remux（毫秒级），仅切点处少量 Recode |
| 画质 | 全片重编码必然有质量损失 | 非切点区域比特流原封搬运，零损失 |
| HEVC 正确性 | 需自行处理 CRA/RASL 花屏问题 | smartcut 已内建 hybrid recode 方案 |
| 多音轨 | 需手动处理每条音轨 | smartcut 自动 passthrough 所有音轨 |
| 代码量 | 命令字符串拼接 + 错误处理 | 一个格式转换函数 + 一行函数调用 |

接口衔接极薄：**MVP** 由 `keep_mask` 与 JSON1 的时间–index 映射在**执行层**内得到保留时间区间，再转为 `list[tuple[Fraction, Fraction]]` 传入 `smart_cut()`（内部与「EDL 区间列表」等价）；smartcut 的 GOP 决策、三路视频处理、时间戳修正等对上层透明。

---

## 3. MVP 数据模型

### 运行时（Layer 2）与 `manifest.py`

- **智能层现行实现**使用内存 **`manifest_dict: dict`**（见 `autosmartcut/intelligence.py`）：自 JSON2 加载后键包括 **`tokens`**（句面）、`goal`、`source`、`comprehension`、`keep_mask`、`review_report`（单条占位）、`human_feedback_history` 等；**不要求** manifest 内携带完整 JSON1 式 `annotations[]`（时间轴仅在 JSON1）。**不与** `TimelineManifest` dataclass 做往返转换。
- **`autosmartcut/manifest.py`** 中的 dataclass 作为 **完整版愿景 / 类型草图** 保留；字段与 dict 不一致时以 **[intelligence-layer2-mvp.md](intelligence-layer2-mvp.md)** 与 **`intelligence_*.py`** 为准。
- **`comprehension`（MVP，dict）**：`purpose`、`outline_blocks[]`（`start_index`/`end_index`/`summary`）、`cleaned_annotations[]`（**稠密全量**，与 **`tokens`/`tokens[]`** 等长；程序按 R2 `corrections` 在句面文本上生成，见 layer2 MVP §5）。
- **`keep_mask`（MVP）**：与 **`tokens`** 等长，`{"index", "keep"}`，`keep` 仅为布尔；智能层对外交付物（JSON3）；**执行层**用 JSON1+JSON3 在内部合成时间区间，**不要求**清单内 `edl[]`（完整版叙事见 `EditDecision` 注释）。
- **`edl[]` / `EditDecision`**：完整版清单字段；MVP 下 Layer 3 **不依赖**智能层落盘的 EDL。详见 `manifest.py` 中 `EditDecision` 文档字符串。

### 阶段间 I/O 契约：三文件格式

三层管道通过三个 JSON 文件交接，每个文件是对应阶段的输出，也是下一阶段的输入基线：

| 文件 | 写入方 | 读取方 | 说明 |
|------|--------|--------|------|
| `layer1_annotations.json`（JSON1） | Layer 1 · perception.py | **Layer 3 · execution.py**（及与 JSON2 对齐校验） | 时间轴与原始句级 `content`；**不是** L2 文件入口 |
| `layer2_input.json`（JSON2） | Layer 1 · perception.py（或工具由 JSON1 生成） | **Layer 2 · intelligence.py** | 智能层**唯一**句面入口：`tokens[]` 仅 `index` + `text` |
| `layer2_output.json`（JSON3） | Layer 2 · intelligence.py | Layer 3 · execution.py | LLM + 人工 overrides 合并后的最终决策 |

#### JSON1 — Layer 1 输出（`layer1_annotations.json`）

```json
{
  "source": "samples/video.mp4",
  "language": "zh",
  "raw_text": "完整 ASR 原文...",
  "annotations": [
    {
      "index": 0,
      "t_start": 0.0,
      "t_end": 15.3,
      "content": "大家好，今天我们来聊深度学习。",
      "gap_after": 1.8,
      "confidence": 0.91
    },
    {
      "index": 1,
      "t_start": 17.1,
      "t_end": 45.0,
      "content": "下面我们看第二个话题。",
      "gap_after": 0.5,
      "confidence": 0.88
    }
  ]
}
```

**约束：** `annotations[].index` 全局唯一且稳定，是 JSON2/JSON3 与执行层跨文件坐标系的基础。智能层以 JSON2 **`tokens[].index`** 与 JSON1 **`annotations[].index`** 对齐（全链须 `len(tokens)==len(annotations)` 且逐条 index 一致）。Layer 3 通过 `index` 合并时间与 `keep_mask`。

#### JSON2 — Layer 2 输入（`layer2_input.json`）

与 JSON1 的句级条数一致；`tokens[]` 仅含 `index` 与 `text`（节约 LLM token）：

```json
{
  "source": "samples/video.mp4",
  "tokens": [
    {"index": 0, "text": "大家好，今天我们来聊深度学习。"},
    {"index": 1, "text": "下面我们看第二个话题。"}
  ]
}
```

**约束：** `len(tokens) == len(JSON1.annotations)`，且 `tokens[i].index == annotations[i].index`。

#### JSON3 — Layer 2 输出（`layer2_output.json`）

LLM 输出的 keep_mask 数组，与人工 overrides 合并后写入此文件，供 Layer 3 消费：

```json
{
  "keep_mask": [
    {"index": 0, "keep": true},
    {"index": 1, "keep": false}
  ]
}
```

**约束：** `save_layer2_json` 当前仅写出 **`keep_mask`**（可与 JSON1 放在同一目录供 Layer 3 引用）。`len(keep_mask) == len(JSON2.tokens)`；全链与 JSON1 对齐时等价于 `len(JSON1.annotations)`。每条 `keep` **仅**为 `true` 或 `false`（**不使用** `null`）。句间间隔由 JSON1 的 `gap_after` 表达。Layer 3 将 JSON3 与 JSON1 对齐后合并保留段：每段右边界为 **该段最后一句** 的 `t_end + min(gap_after, gap_after_cap)`（默认见 `config.toml` 的 `[execution]`）；再经 `pre_pad` / `post_pad`；**可选**经 **VAD 切点吸附（Snap）** 微调入/出点；再经 `min_duration` 合并等后处理。

#### 切点参数默认值

Layer 3 在从 keep_mask 编译时间区间时应用以下参数（CLI 与 `config.toml` 可覆盖；编排见 `runner.py`）：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `pre_pad` | 0.15s | 每个保留段起点向前扩展 |
| `post_pad` | 0.25s | 每个保留段终点向后扩展 |
| `min_duration` | 1.0s | 过短段合并到相邻段阈值 |

**`[execution]` 中 VAD 切点吸附（可选）**

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `vad_snap_enabled` | `true` | 总开关；`ascut run` 使用 **`--no-vad-snap`** 时**忽略**本节所有 VAD 项并关闭吸附 |
| `vad_snap_radius` | `0.12` | 入点/出点各自在 ±radius（秒）内搜索静音并吸附 |
| `vad_threshold` | `0.35` | Silero `get_speech_timestamps` 的 `threshold` |
| `vad_min_silence_ms` | `80` | Silero `min_silence_duration_ms` |
| `vad_speech_pad_ms` | `10` | Silero `speech_pad_ms` |

实现要点（`autosmartcut/vad_silence.py` + `timeline_segments.keep_mask_to_positive_segments`）：从源视频 **单独解码** 16 kHz mono 波形（不写盘）；ONNX Silero 在 CPU 上推理；语音段的补集为静音区间；在 `apply_padding` **之后**对每个保留段的左右边界做 Snap；单边未命中则保持该边原值；若吸附后区间非法则整段回滚；再合并重叠区间并 clamp 到 `[0, duration]`。VAD 构建失败时记录 warning 并退回无吸附路径。

---

### 检查点目录结构（规划，尚未实现）

以下目录与文件名为 **D11 设计约定**，供未来 `checkpoint.py` + `runner.py` 落地；**当前仓库未写入**。

```
video_dir/
├── interview_final.mp4
└── .autosmartcut_interview_final/       ← 与视频同目录，携带视频名
    ├── manifest.json                    ← 当前最新完整清单
    ├── manifest.layer1.json             ← Layer 1 完成后快照
    ├── manifest.layer2.r0.json          ← Layer 2 第 0 轮快照
    ├── manifest.layer2.r1.json          ← 后续轮次（完整版含 [r] 反馈重跑时）
    └── manifest.layer3.json             ← Layer 3 完成后快照
```

**MVP 现行**无多轮 `[r]`；若未来接入完整版人工反馈，轮次 `r1+` 语义仍以架构文档为准。当前仅用独立 JSON1/JSON3 文件手工串联时，不依赖此目录。

### 人工反馈历史压缩示例（N=1，完整版叙事；MVP 无 `[r]`）

```
第 1 轮 [r] 后：
  human_feedback_history = [
    {round: 1, feedback: "开场白应保留，包含核心论点预告", summary: ""}
  ]

第 2 轮 [r] 后（超过 N=1，第 1 轮压缩为摘要）：
  human_feedback_history = [
    {round: 1, feedback: "",  summary: "用户要求保留开场白（含核心论点预告）"},
    {round: 2, feedback: "所有广告段无论内容都应删除", summary: ""}
  ]

发送给 LLM 的上下文：
  历史反馈摘要：
    第1轮：用户要求保留开场白（含核心论点预告）
  最近反馈（原文）：
    第2轮：所有广告段无论内容都应删除
```

---

## 4. Demo 验证方案

### Demo 阶段目标

Demo 阶段先于 MVP 工程化执行，目标是用最少代码验证最大风险点。Demo 脚本不引入完整架构——没有 TimelineManifest、没有检查点、没有管道编排——只有聚焦于单一技术问题的独立脚本。

按管道三层切为三组：

| Demo | 对应层 | 状态 |
|------|--------|------|
| Demo 1 | 识别层（Qwen3-ASR + 字级对齐 + 句级聚合） | ✅ 已完成并验证 |
| Demo 2 | 智能层（2a→2b→2c 占位→2d） | ✅ `demos/demo2_llm.py` 封装 JSON2→JSON3（默认跳过 2d）；等价 `python -m autosmartcut.intelligence` |
| Demo 3 | 执行层（JSON3 → keep_mask → smartcut 库集成） | ✅ 已完成并验证 |

智能层逻辑以 `intelligence_*.py` 与 `intelligence-layer2-mvp.md` 为准；可按 Demo 2 章节做独立回归。

### Demo 依赖关系

**关键路径：** Demo 1 →（智能层可经 `python -m autosmartcut.intelligence` 验证）→ Demo 3 全链路 json 模式。

Demo 1 与 Demo 3 可并行验证；端到端冒烟：**JSON2** → `intelligence` 模块（或 `demo2_llm.py`）→ JSON3 → `demo3_smartcut.py json`（JSON1+JSON3）；JSON2 可由 L1 写出或与 JSON1 同批生成。

---

### Demo 1：识别层 — Qwen3-ASR + 字级对齐 + 句级聚合

**文件：** `demos/demo1_asr.py`

**目的：** 验证 Qwen3-ASR + ForcedAligner 在中文视频上的实际可用性，确认三件事：

1. **Qwen3-ASR-1.7B 中文转写质量**：专有名词和同音字的误识率是否在 2a 的错词候选 / `corrections` + 程序替换可覆盖的范围内。
2. **Qwen3-ForcedAligner-0.6B 字级对齐精度**：`char_timestamps` 完整率与字级误差，验证 ~0.1s 精度基线（D5 已确认方向，此 Demo 取得实测数据）。
3. **句级聚合节点行为**：按 D16 规则切分后的句级标注边界是否对应自然语义单元。

无 ASRAdapter 协议，无多实现对比——Qwen3-ASR 是唯一实现，直接调用，不抽象。

#### vLLM 启动（spawn guard）

Qwen3-ASR 使用 vLLM 作为 inference backend，Windows 下多进程需要 spawn guard：

```python
if __name__ == "__main__":
    # vLLM 在 Windows 下使用 spawn 启动子进程，
    # 所有入口逻辑必须在此守卫内，否则子进程重入时重复执行
    main()
```

> **适用范围：** spawn guard 同样适用于 Demo 2 和 MVP 的 `runner.py` 入口。ForcedAligner 走 transformers 后端，不受 vLLM spawn 影响，但 ASR 主模型走 vLLM，guard 必须保留。

#### 做什么

1. 准备 3–5 段不同类型的中文视频（播客、课程、vlog，各 5–10 分钟），用 PyAV 预提取 16kHz 单声道 WAV。
2. 用 Qwen3-ASR-1.7B 对每段音频转写，记录耗时和主观质量（同音字错误、专有名词识别情况）。
3. 用 Qwen3-ForcedAligner-0.6B 对转写文本做字级强制对齐，产出 `char_timestamps[]`。
4. 验证 `char_timestamps` 完整性：每个汉字是否都有对应 `{text, start, end}`，无缺失、无乱序。
5. 验证字级误差：随机抽取 20–30 个字，对照音频手动核查时间戳偏差，统计中位误差。
6. 运行句级聚合节点（按 D16 规则），人工核查切分结果是否符合语义直觉。
7. 输出标准化标注 JSON，格式与 `Annotation` dataclass 一致：

```json
[
    {
        "index": 0,
        "t_start": 0.0,
        "t_end": 15.3,
        "content": "大家好，今天我们来聊深度学习。",
        "gap_after": 1.8,
        "confidence": 0.91,
        "metadata": {
            "char_timestamps": [
                {"text": "大", "start": 0.00, "end": 0.18},
                {"text": "家", "start": 0.18, "end": 0.35}
            ]
        }
    },
    {
        "index": 1,
        "t_start": 17.1,
        "t_end": 45.0,
        "content": "下面我们看第二个话题。",
        "gap_after": 0.5,
        "confidence": 0.88,
        "metadata": {}
    }
]
```

> **字段名说明：** `char_timestamps` 中的 `start`/`end` 是 perception 层从 `ForcedAlignItem.start_time`/`end_time` 归一化而来，单位为秒（float），与 `Annotation.t_start`/`t_end` 保持一致。

#### 验证什么

| 验证项 | 通过标准 |
|--------|----------|
| 转写质量 | 主要内容可辨认，同音字误识在 2a 错词管线（候选 → 唯一替换 → 程序应用）可修正范围内 |
| char_timestamps 完整率 | > 95% 的汉字有有效时间戳（无空值、无缺失） |
| 字级对齐精度 | 随机抽样中位误差 < 200ms |
| 句级聚合边界 | 切分处对应自然句边界（人工目视确认 ≥ 90%） |
| gap_after 合理性 | 明显长停顿在相邻句的 `gap_after` 上有体现（可与字级时间对照） |
| 长音频行为 | 30 分钟以上音频是否需要分段处理 |
| vLLM spawn guard | Windows 下脚本正常启动，无子进程重入报错 |

#### Go/No-Go

- **Go：** char_timestamps 完整率 > 95%，字级误差 < 200ms → 确认 D3（Qwen3 组合）与 D5（句间间隔）可用于 MVP。
- **Pivot：** 完整率 ≤ 95% 但误差可接受 → 在句级聚合时对缺失字符做插值处理（均分相邻字间距），不阻塞 MVP。
- **Stop：** 字级对齐整体误差 > 500ms → 评估重新微调 ForcedAligner，或改用其他对齐方案。

#### 对后续决策的影响

- 确认 D3（模型组合）的实际可用性。
- char_timestamps 完整率结果决定 D16 句级聚合是否需要字符插值兜底逻辑。
- vLLM spawn guard 验证结果决定 Demo 2 和 MVP 的进程启动方式。

---

### Demo 2：智能层 — 三次 LLM + 程序替换

**文件：** `demos/demo2_llm.py`

**覆盖子阶段：** 2a（R1 LLM + R2 LLM + **程序** `apply_corrections`）、2b（一次 LLM，输出 `keep_mask`）。2c auto-pass 与 2d CLI 不在本 Demo 范围内时，可省略。

**契约依据：** [intelligence-layer2-mvp.md](intelligence-layer2-mvp.md) §5、§9。

**当前状态：** Demo 1、Demo 2 脚本与 Demo 3 已对齐现行契约；示例 JSON2 常为仓库下 `output/layer2_input.json` 或 `outputs/layer2_input.json`（`tokens[]` 仅 `index` + `text`，与 `build_layer2_input_document` 一致）。

**输入：** 任意合法 JSON2；亦可由 JSON1 现场调用 `build_layer2_input_document` 生成等价 `tokens` 再写入文件供 L2 读取。

#### 2a Round 1（粗理解，仅内存）

- **输入：** `tokens[]` + `goal`（与 JSON2 一致）。
- **期望 JSON（LLM）：** `purpose_rough`、`outline_blocks_rough[]`（`start_index`/`end_index`/`topic`）、`candidate_misrecognitions[]`（`index`、`original`：`[词, 句内起始字符下标]`、`suggestions[]`）。
- **验证项：** 可解析率；粗分块 index 范围合理；错词候选是否覆盖已知专名误识。

#### 2a Round 2（精化 + 替换表，仅内存）

- **输入：** 同 Round 1 的 `tokens`，外加 Round 1 的 `purpose_rough`、`outline_blocks_rough`、`candidate_misrecognitions`。
- **期望 JSON（LLM）：** `purpose`、`outline_blocks[]`（`start_index`/`end_index`/`summary`）、`corrections[]`（`index`、`original`、`corrected`）。
- **验证项：** `corrections` 与候选坐标一致；分块 `summary` 可供 2b 使用。

#### 2a 程序步骤（写入 `comprehension`）

- 根据 `corrections` 在 **`tokens[].text` 的只读视图/副本**上做字符级替换（与 Prompt 坐标一致），再稠密化为 **`cleaned_annotations[]`**；**不**改写磁盘上的 JSON1，也不修改已加载的 **`tokens[]` 原文**（消歧结果进 `comprehension`）。
- **验证项：** 有误识的 index 是否出现 `cleaned_content`；无替换时列表可为空。

#### 2b（决策）

- **输入：** `comprehension.purpose`、`outline_blocks`、`cleaned_annotations` + **`tokens[]`**（由 Prompt 构造全量 `[index] text` 列表）。
- **期望 JSON（LLM）：** `keep_mask[]`；`checklist_coverage[]` 可为空数组（MVP 预留）。
- **约束：** `len(keep_mask) == len(tokens)`（全链与 JSON1 一致时等于 `len(JSON1.annotations)`）。

#### 验证什么

| 验证项 | 通过标准 |
|--------|----------|
| LLM JSON 可解析率 | 10/10 次不崩溃（各轮独立或端到端） |
| `keep_mask` 完整性 | 长度与句级条数一致，index 对齐 |
| 消歧有效性 | 程序生成的 `cleaned_annotations` 相对原文有期望修正（人工目视）|
| R1→R2 上下文 | 同一对话：`assistant` 承载 R1 JSON；R2 `user` 引用上一轮并带全量句面（见 [intelligence-layer2-mvp.md](intelligence-layer2-mvp.md) §5.5） |

#### Go/No-Go

- **Go：** 三次 LLM + 程序替换跑通，`keep_mask` 无遗漏 → Prompt/schema 定形，复用到 `intelligence_2a.py` / `intelligence_2b.py`。
- **Pivot：** JSON 偶发错误 → `call_llm_structured` / `call_turn_structured` 内建重试（已有）；schema 非法不重试。
- **Stop：** `corrections` 长期无法落地（坐标与中文分词约定不一致）→ 统一字级下标约定并调整 Prompt/程序；或 keep_mask 系统性异常 → 调整 2b Prompt。

#### 对后续决策的影响

- 2b 产出的 `layer2_output.json`（JSON3）可直接用于 Demo 3 的 json 模式（与 JSON1 同目录或显式传参）。
- 超长文本的分块策略由 `outline_blocks` / 滑窗等单独实验，**不**依赖 MVP checklist。

---

### Demo 3：执行层 — smartcut 库集成

**文件：** `demos/demo3_smartcut.py`

**目的：** 验证 `keep_mask → 时间区间 → smartcut` 完整链路，确认 Layer 3 在密集切点与真实 JSON1/JSON3 输入下均可正常工作。

Demo 有两种运行模式：`dense`（合成密集 EDL 压测）和 `json`（读取真实 JSON1+JSON3 走完整 keep_mask 管线）。

#### 做什么

**dense 模式（`python demos/demo3_smartcut.py dense --input samples/video.mp4`）：**
1. 合成密集 EDL（52 个 keep 段，每段 ~2.5 秒，keep/cut 交替）压测 smartcut 边界行为。
2. 不依赖 Layer 1/2 输出，聚焦 Layer 3 本身的极端条件。

**json 模式（路径按本机调整，示例：`python demos/demo3_smartcut.py json --layer1 output/layer1_annotations.json --mask output/layer2_output.json`）：**
1. 读取 JSON1（`layer1_annotations.json`）获取 annotations 与视频路径。
2. 读取 JSON3（`layer2_output.json`）获取 keep_mask。
3. 调用 `execution.positive_segments_from_mask_files()` 走完整管线：`keep_mask` 与 JSON1 句级时间合并 → 时间区间 → padding →（可选）VAD **Snap** → 重叠合并与边界 clamp → `min_duration` 合并 → `Fraction[]`。可加 **`--no-vad-snap`**、**`--config`**。
4. 调用 `smart_cut()` 输出视频。

人工核查输出视频：画面质量、音视频同步、H.265 切点处是否有花屏。

#### 验证什么

| 验证项 | 通过标准 |
|--------|----------|
| 密集切点（50+ 段） | 全部正确输出，进程不崩溃 |
| 短片段（2–3 秒） | 无音视频不同步 |
| Fraction 精度传递 | 浮点时间戳转换后无切点偏移（人工目视确认） |
| H.264 / H.265 / MKV | 三种格式均正确输出 |
| H.265 花屏 | 切点处无 RASL 花屏（smartcut hybrid recode 生效） |
| 多音轨 | 所有音轨按 keep 区间同步保留 |
| 库接口签名 | `MediaContainer` 构造方式与 `smart_cut()` 参数与文档一致 |

#### Go/No-Go

- **Go：** dense 与 json 两种模式均正常工作 → `execution.py` 的完整链路已验证，可直接套用到 MVP `execution.py`。
- **Pivot：** 短片段有音视频不同步 → 在 `keep_mask_to_positive_segments()` 的 `min_duration` 参数中调高阈值（默认 1.0s），合并过短相邻保留段。
- **Stop：** smartcut 在密集切点下崩溃 → 评估 fork 修复；若不可行则降级到 FFmpeg subprocess 方案（`execution.py` 接口不变，只换执行后端）。

#### 对后续决策的影响

- `execution.keep_mask_to_positive_segments()` 接口已锁定，直接用于 MVP `execution.py`（含可选参数 `silence_intervals` / `snap_radius`，通常由 `positive_segments_from_mask_files` 注入）。
- `pre_pad`/`post_pad`/`min_duration` 与 VAD 相关默认值可通过 json 模式与听感迭代调整。

---

## 5. 仓库结构（与当前 ascut 仓库一致）

```
ascut/  （或 AutoSmartCut/）
├── demos/
│   ├── demo1_asr.py              ← Layer 1 验证（Qwen3-ASR + 对齐 + 句级聚合）
│   ├── demo2_llm.py              ← Layer 2：JSON2→JSON3（封装 `intelligence.run_intelligence_layer`）
│   ├── demo3_smartcut.py         ← Layer 3：dense / json 模式
│   └── tools/gen_demo_jsons.py 等（辅助，非 L1/L2/L3 环节演示）
├── autosmartcut/
│   ├── config.py
│   ├── manifest.py               ← dataclass 草图（见 §3）；运行时 Layer2 用 dict
│   ├── perception.py             ← Layer 1：音频解码、聚合、gap_after、build_layer2_input_document
│   ├── vad_silence.py            ← Layer 3：16k 解码（内存）+ Silero VAD → 静音区间；Snap 纯函数
│   ├── execution.py              ← Layer 3：JSON1+JSON3 → 区间（含可选 VAD）→ Fraction → smartcut
│   ├── intelligence.py           ← Layer 2 编排：读 JSON2 / 写 JSON3，串联 2a–2d
│   ├── layer2_tokens.py          ← JSON2 校验与 `source`→视频路径解析
│   ├── intelligence_2a.py
│   ├── intelligence_2b.py
│   ├── intelligence_2c.py        ← MVP 占位 pass
│   ├── intelligence_2d.py        ← 2d CLI（t / a / q）
│   ├── intelligence_llm.py       ← OpenAI 兼容 DeepSeek；单轮/多轮结构化；jsonschema；思考模式参数屏蔽
│   └── runner.py                 ← `ascut run`：三层编排与 `--from-stage`
├── doc/
│   ├── AutoSmartCut.md
│   ├── AutoSmartCut-MVP.md
│   └── intelligence-layer2-mvp.md
├── config.toml
└── pyproject.toml
```

**说明：** 无 `stages/` 子包；`checkpoint.py` 仍为规划项。Qwen3-ASR 可通过 `qwen-asr`（PyPI）或本地 editable 安装，以实际环境为准。

---

## 6. 各阶段实现要点（现行代码）

| 层 | 模块 | 要点 |
|----|------|------|
| L1 | `perception.py`、`demo1_asr.py` | PyAV 解码音频；Qwen3-ASR + ForcedAligner；句级聚合；`gap_after`；可写 JSON1 / 内存 dict |
| L2 | `intelligence.py`、`layer2_tokens.py`、`intelligence_2a/b/c/d.py`、`intelligence_llm.py` | 读 **JSON2** → `manifest_dict`（**`tokens`** + `comprehension`）；2a **R1 单轮 + R2 多轮第二跳** + 程序 `cleaned_annotations`；2b 单轮 `keep_mask`；2c 占位；2d 文本 CLI |
| L3 | `execution.py`、`vad_silence.py`、`demo3_smartcut.py` | `positive_segments_from_mask_files`（可选 VAD）；`keep_mask_to_positive_segments`（`timeline_segments`）；`MediaContainer` + `smart_cut` |

详细契约以 [intelligence-layer2-mvp.md](intelligence-layer2-mvp.md) 为准。

---

## 7. CLI（现状与规划）

**已实现**

- Layer 2：`python -m autosmartcut.intelligence <layer2_input.json> <output.json> [--goal "..."] [--auto] [--verbose] [--two-b-mode single|chunked]`
- 等价：`python demos/demo2_llm.py --layer2 <JSON2> --output <JSON3> ...`（默认跳过 2d；`--interactive-2d` 启用审阅）
- 编排：`ascut run` 或 `python -m autosmartcut.runner run`（`--from-stage 1|2|3`，stage 2 须 `--layer2-json`）；**`--no-vad-snap`** 关闭 L3 切点吸附（忽略 `config.toml` 中 VAD 项）
- Layer 1 / 3：见 `demos/demo1_asr.py`、`demos/demo3_smartcut.py` 的参数说明（Demo3 json 模式：`--no-vad-snap`、`--config`）

**规划中（未实现，需求保留）**

- `--resume`、`inspect`、`config` 等子命令；`checkpoint.py` 检查点目录

---

## 8. 依赖清单

以仓库根目录 **`pyproject.toml`** 为准，当前示例：

```toml
[project]
name = "autosmartcut"
requires-python = ">=3.11"
dependencies = [
    "qwen-asr",
    "smartcut",
    "jsonschema>=4,<5",
    "openai>=1.0",
    "numpy>=1.24",
    "av>=12",
    "silero-vad>=5.0",
    "torch>=2.0",
    "onnxruntime>=1.16",
]

[project.optional-dependencies]
dev = ["pytest>=7"]
```

`intelligence_llm.py` 使用标准库 **`tomllib`** 读取 `config.toml`，不再依赖第三方 `toml` 包。

**说明**

- Layer 1 依赖 Qwen3 模型权重（本地路径，见 `demo1_asr.py` / `config.toml`）。
- PyAV 随 `smartcut` 间接可用；`perception` 与 smartcut 共用 PyAV 体系。
- Layer 3 可选切点吸附依赖 **`silero-vad`**（ONNX，默认 CPU）、**`torch`**、**`onnxruntime`**；与 L1 ASR **不同时运行**，一般不抢占 GPU。

---

## 9. MVP 之后的第一批扩展

以下功能**不在 MVP 范围**，但架构已预留空间，实现时不需要修改主干。

| 扩展项 | 影响的阶段 | 实现方式 |
|--------|-----------|----------|
| 字幕文件生成（SRT/ASS） | Stage 4 新增节点 | 基于 EDL 重新对齐 ASR 时间戳，输出字幕文件；smartcut 已有字幕轨 passthrough，可直接扩展 |
| WhisperX 切换 | Stage 1 适配器替换 | 新增 `WhisperXAdapter` 实现 `ASRAdapter` 协议；获得更精确的词级时间戳 + 说话人分离 |
| 声纹识别 / 说话人标注 | Stage 1 新增节点 | 通过 WhisperX 或独立说话人分离模型，在 annotations 中添加 `speaker_id` 字段 |
| GUI 审阅界面 | 2d 替换 CLI | 与现行逻辑等价：编辑 **index 级 `keep_mask`**（及 overrides），定稿后仍走 JSON3 + Layer 3 |
| 多剪辑策略预设（Preset） | Layer 2 | 将不同目标封装为预设，自动填充 `--goal` 等；仍输出 `keep_mask` |
| 多文件批量处理 | 管道编排层 | `runner.py` 增加队列逻辑；每个文件独立检查点 |
| 图像识别 / 去广告 | Stage 1 新增节点 | 目标检测等产出标注；Layer 2 决策阶段可将对应 index 压为 `keep=false` 等策略 |
| Agent Loop 自动审阅 | Stage 5（新增） | vLLM 检查成片后生成修改建议，自动回到 Stage 3 迭代；人工只做最终确认 |

---

*文档版本：0.3.2*  
*修订日期：2026-04-10（对齐现行 ascut：L2 入口 JSON2/`tokens`；**2a R1+R2 真多轮 LLM API**；`runner` 编排、L3 keep_mask + 可选 VAD 切点吸附、`--no-vad-snap`；checkpoint 仍规划）*  
*对应架构愿景：[AutoSmartCut.md](AutoSmartCut.md)*
