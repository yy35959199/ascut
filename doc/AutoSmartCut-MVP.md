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
| Layer 1 识别层 | Qwen3-ASR-1.7B 转写 + Qwen3-ForcedAligner-0.6B 字级对齐 + 句级聚合 + 间隙静音推导 | 标注列表（Annotations） |
| Layer 2 智能层 | DeepSeek LLM（2a 固定两轮 + 2b 独立一次调用）+ 2c auto-pass + 2d CLI 人工审阅 | 语义理解（Comprehension）+ 语义片段（Segments）+ 剪辑决策表（EDL） |
| Layer 3 执行层 | smartcut 库（帧精确剪切） | 最终视频文件 |

### Layer 2 在 MVP 中的简化

智能层在完整架构中包含四个子阶段（2a 理解→2b 决策→2c 审核→2d 人工）和双层循环。MVP 阶段做如下简化：

| 子阶段 | 完整架构 | MVP 实现 |
|--------|----------|----------|
| 2a 理解 | 固定两轮：Round 1（bootstrap）产出粗糙主旨 + 符号表；Round 2（印证）产出消歧标注 + 精化主旨 + 检查清单 | **与完整架构相同，不简化**；两轮均为必要步骤，不可合并 |
| 2b 决策 | 独立一次 LLM 调用，读 `cleaned_annotations` | **与完整架构相同**；MVP 共计 3 次 LLM 调用（2a×2 + 2b×1） |
| 2c 审核 | LLM 对照检查清单做结构化审核，输出 pass / fix_decision / fix_checklist | **代码层自动 pass**：直接写入一条 `verdict="pass"` 的 `ReviewReport` |
| 2d 人工 | CLI 交互 | 与完整架构相同：审阅 / 手动切换 / 自然语言反馈 / 确认 |
| 循环 | max_inner / max_outer 按 Token 预算动态分配 | **完全禁用**（max_inner=0, max_outer=0），人工兜底 |

这意味着 MVP 的 `loop_metadata` 每次都是 `inner_rounds=0, outer_rounds=0, final_verdict="pass"`，字段存在只为保证 schema 与完整版兼容。

### MVP 边界

**包含：**
- 三层全链路贯通
- 时间轴清单（TimelineManifest）JSON 序列化与检查点
- Layer 2 人工介入（2d）：自然语言反馈重跑智能层、手动切换片段保留状态、确认生成 EDL
- smartcut 作为执行后端（帧精确、HEVC 正确性）
- 中文 CLI 界面

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
| D5 | 静音检测方式 | **已确认**：从字级时间戳推导（末字结束时间 → 首字开始时间），精度 ~0.1s，不引入 Silero VAD | 字级对齐已提供足够精度；间隙推导基于 `char_timestamps`，比段级间隙更精确；无需额外依赖 |
| D6 | 首选 LLM | DeepSeek（V3/R1，OpenAI 兼容协议） | 中文理解好，成本极低，API 格式与 OpenAI 兼容，一个适配器覆盖大多数提供商 |
| D7 | LLM 分析目标传入 | CLI 参数 `--goal "..."` | LLM 需要明确目标才能做有意义的相关性评分；目标因场景差异大，由用户指定 |
| D8 | CLI 界面语言 | 中文 | 目标用户为中文使用者 |
| D9 | 人工反馈历史策略 | 保留所有轮次，超过 N 轮时压缩为摘要；N 可配置，Demo 阶段 N=1 | 累积上下文使 LLM 越来越了解用户意图；压缩机制控制 Prompt 长度 |
| D10 | smartcut 依赖方式 | `pip install smartcut`（PyPI 1.7） | 稳定版本；PyAV 通过 smartcut 间接引入，统一 FFmpeg 集成方式 |
| D11 | 检查点存储位置 | 与输入视频同目录，子目录名携带视频名（如 `.autosmartcut_video_name/`） | 便于关联，不污染其他目录；携带视频名避免多文件冲突 |
| D12 | 批量处理 | MVP 只支持单文件 | 降低 MVP 复杂度；单文件场景已足够验证核心流程 |
| D13 | 智能层 LLM 调用策略 | 2a 固定两轮（bootstrap + 印证），2b 独立一次，MVP 共计 3 次 LLM 调用；2c 仍为 auto-pass | 2a 两轮是架构核心（符号表 + 消歧标注），不可压缩；2b 需在 2a 两轮完成后才能读 `cleaned_annotations`；manifest 字段结构与完整版相同，无需未来改 schema |
| D14 | 2c 审核子阶段 | MVP 中代码层自动 pass，不调用 LLM | 循环禁用时 2c 无实质作用；保留 `ReviewReport` 字段结构与完整版兼容；人工在 2d 承担审核兜底 |
| D15 | Qwen3-ASR 安装方式 | editable install：`pip install -e "./Qwen3-ASR[vllm]"`；不使用预打包版本 | Qwen3-ASR 仓库作为子目录存在于工作区，editable install 保证本地修改立即生效；选 vLLM extra 以启用高性能 inference backend |
| D16 | 句级聚合分割规则 | 分割模式：`punctuation`（默认）或 `timing`；punctuation 模式以句终标点（。！？；\n）为分割依据，timing 模式以停顿阈值 `split_pause_threshold=0.20s` 为分割依据；单句字数上限 `max_chars=60` 兜底；间隙 ≥ `silence_threshold=0.80s` 独立推导为静音标注 | 实验确认 punctuation 模式切分结果显著优于 timing 模式，已设为默认；`split_pause_threshold` 与 `silence_threshold` 是两个独立阈值：前者决定句级分割，后者决定是否插入静音标注 |
| D17 | LLM 决策粒度 | LLM 通过 `keep_mask`（布尔索引数组）输出决策，不输出带时间戳的 Segment | 去掉时间戳后 token 消耗降低约 5×；LLM 只需处理文本索引，时间戳映射由 Layer 3 从 JSON1 完成；静音标注不参与 LLM 决策（`keep: null`），由规则推导：两侧 speech 均保留则静音保留 |

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

接口衔接极薄：将时间轴清单的 EDL（剪辑决策表）转换为 `list[tuple[Fraction, Fraction]]` 后直接传入 `smart_cut()`，smartcut 内部的 GOP 决策、三路视频处理、时间戳修正等全部透明。

---

## 3. MVP 数据模型

### 数据类定义

```python
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class SourceMedia:
    path: str                    # 相对或绝对路径
    duration: float              # 总时长（秒）
    audio_track_index: int = 0   # 用于 ASR 的音轨索引


@dataclass
class Annotation:
    """Layer 1（perception.py）写入：ASR 识别和静音推导的原始事实记录。
    只记录机器观测到了什么（这里有语音/静音），不承载语义判断。"""
    index: int          # 全局唯一序号（0-based），JSON1/JSON2/JSON3 跨文件对齐的坐标
    t_start: float
    t_end: float
    type: str           # "speech" | "silence"
    content: str        # 转写文字（silence 时为空字符串）
    confidence: float   # Qwen3-ASR 产出的置信度信号
                        # 仅作为模型可靠性参考信号，不用于 UI 显示
    metadata: dict = field(default_factory=dict)
    # 开放式扩展字段；char_timestamps: [{"text": str, "start": float, "end": float}]
    # 由 Qwen3-ForcedAligner 产出，是精确切点、精确字幕和语气词剪除的基础
    # 注意：ForcedAlignItem 原始字段名为 start_time/end_time（毫秒→秒后），
    # perception 层归一化为 start/end 后写入此处


@dataclass
class ChecklistItem:
    """comprehension.checklist[] 的单条条目。"""
    item: str
    priority: str       # "must" | "optional"
    covered: bool = False   # 2b 决策时由 LLM 填写；完整版 2c 审核时独立验证


@dataclass
class SymbolTableEntry:
    """comprehension.symbol_table[] 的单条条目。
    2a Round 1（bootstrap）产出，记录 ASR 可能的误识形式与正确形式的对应关系。"""
    term: str               # 正确形式（如"张伟"）
    raw_form: str           # ASR 可能的误识形式（如"章维"）
    category: str           # "person" | "term" | "entity" | "other"
    first_occurrence: int   # 首次出现的 annotation_index（0-based）


@dataclass
class CleanedAnnotation:
    """comprehension.cleaned_annotations[] 的单条条目。
    2a Round 2（印证）产出，在不修改原始 annotations[].content 的前提下追加消歧文本。
    遵循 Append-only：原始转写永久保留，消歧文本供 2b/2c 消费。"""
    annotation_index: int   # 对应 annotations[] 的下标（0-based）
    cleaned_content: str    # 消歧后的文本（同音字纠正、专有名词还原等）


@dataclass
class Comprehension:
    """Layer 2 / 2a 理解子阶段产出（固定两轮：Round 1 bootstrap + Round 2 印证）。
    存储对内容的整体语义理解，是 2b 决策和 2c 审核的评估基准。
    symbol_table 和 cleaned_annotations 由 2a 追加写入，不修改 Layer 1 原始标注（Append-only）。"""
    purpose: str
    checklist: list[ChecklistItem] = field(default_factory=list)
    symbol_table: list[SymbolTableEntry] = field(default_factory=list)           # Round 1 产出
    cleaned_annotations: list[CleanedAnnotation] = field(default_factory=list)   # Round 2 产出
    content_map: list = field(default_factory=list)      # 叙事图谱，中期扩展预留，MVP 置空


@dataclass
class KeepMaskEntry:
    """Layer 2 / 2b 决策子阶段 JSON 输出中的单条条目（JSON3 格式）。
    LLM 只输出布尔决策，不输出时间戳——时间戳由 Layer 3 从 JSON1 反查。
    静音条目不参与 LLM 决策，keep=None 表示由规则推导（两侧 speech 均保留则静音保留）。"""
    index: int          # 对应 JSON1 annotations[].index，跨文件对齐坐标
    keep: bool | None   # True=保留, False=删除, None=静音（不由 LLM 决策）


@dataclass
class Segment:
    """Layer 2 / 2b 决策子阶段写入：LLM 决策记录，按 keep_mask 条目组织。
    MVP 中 [t] 操作将变更追加到当前 HumanFeedbackRound.overrides（delta 模式，不原地修改 keep_mask）；
    最终有效决策 = keep_mask + 累积 overrides 合并推导（AI决策叠加人工delta）。
    发送给 LLM 重跑时，将 keep_mask 与所有 overrides 合并后作为上一轮决策基线传入。"""
    keep_mask: list[KeepMaskEntry] = field(default_factory=list)
    # 当前轮 LLM 输出的完整决策数组，长度等于 JSON1 annotations 总数
    label_map: dict[int, str] = field(default_factory=dict)
    # index → 主题标签，供 CLI 审阅界面展示（可选，LLM 顺带输出）
    summary_map: dict[int, str] = field(default_factory=dict)
    # index → 内容摘要，供 CLI 审阅界面展示（可选，LLM 顺带输出）


@dataclass
class ReviewReport:
    """Layer 2 / 2c 审核子阶段产出。
    MVP 中由代码层自动生成（verdict 固定为 "pass"，coverage_issues 为空列表）。
    字段结构与完整版保持一致，未来启用真实 LLM 审核时只修改生成逻辑，不改 schema。"""
    round: int
    verdict: str                             # "pass" | "fix_decision" | "fix_checklist"
    coverage_issues: list[str] = field(default_factory=list)
    completeness_issues: list[str] = field(default_factory=list)
    token_spent: int = 0


@dataclass
class EditDecision:
    """Layer 2 / 2d 人工子阶段写入：最终剪辑决策，由 segments_to_edl() 在 [a] 确认时编译生成。
    Layer 3 只读此表，不再回溯 segments[]。"""
    t_start: float
    t_end: float
    action: str         # "keep" | "cut"


@dataclass
class LoopMetadata:
    """Layer 2 循环控制元数据。
    MVP 中 inner_rounds=outer_rounds=0（循环禁用），final_verdict 固定为 "pass"。
    字段存在只为保证 schema 与完整版兼容。"""
    total_token_spent: int = 0
    inner_rounds: int = 0
    outer_rounds: int = 0
    final_verdict: str = ""   # "pass" | "budget_exceeded" | "max_rounds"


@dataclass
class HumanFeedbackRound:
    """Layer 2 / 2d 人工子阶段写入：每次 [r] 反馈或 [a] 确认触发追加一条。
    超过 N 轮时旧轮次的 feedback 置空并压缩进 summary，
    控制发给 LLM 的上下文长度（N 由 intelligence.max_raw_rounds 配置）。"""
    round: int
    verdict: str        # "confirm"（路径④ [a] 确认）| "feedback"（路径③ [r] 反馈）
    overrides: list[dict] = field(default_factory=list)
    # [t] 操作产生的 delta 记录（bitmap 模式，不原地修改 keep_mask）：
    # [{"index": int, "keep": bool}]，每条为对某个 annotation 的人工覆盖决策。
    # 最终有效决策 = keep_mask + 本轮及历史所有 overrides 合并推导（later 覆盖 earlier）。
    # 发送给 LLM 重跑时，AI 决策与累积人工 delta 合并后作为上一轮基线传入，
    # 保证 LLM 感知到完整的当前状态。
    feedback: str = ""  # 原文反馈（最近 N 轮保留，更早的置为空字符串；verdict=confirm 时为空）
    summary: str = ""   # 压缩摘要（超过 N 轮后填入，否则为空字符串）
    timestamp: str = "" # ISO 8601 格式


@dataclass
class TimelineManifest:
    """贯穿全管道的唯一共享状态。各层只读/写此结构，由 runner.py 串联。
    设计保证：每层只追加自己负责的字段，不覆写上游字段（append-only）。
    可完整序列化为 JSON，支持断点恢复和审计追溯。"""

    # --- runner.py 初始化时填入 ---
    version: str                          # schema 版本，用于未来格式迁移兼容性判断，如 "0.1.0"
    source_media: SourceMedia             # 输入视频元信息（路径、时长、音轨索引）

    # --- Layer 1（perception.py）写入 ---
    annotations: list[Annotation] = field(default_factory=list)
    # 识别层产出：speech/silence 逐条标注，含时间戳和 ASR 置信度

    # --- Layer 2 / 2a（intelligence.py）写入，固定两轮 ---
    comprehension: Comprehension | None = None
    # 理解子阶段产出：主旨 + 符号表 + 消歧标注 + 检查清单（Round 1 + Round 2 分别写入）
    segments: list[Segment] = field(default_factory=list)
    # 决策子阶段产出（2b 独立一次 LLM 调用）：语义片段列表，每轮人工 [r] 反馈后整体替换

    # --- Layer 2 / 2c（intelligence.py）写入 ---
    review_reports: list[ReviewReport] = field(default_factory=list)
    # 审核报告列表：每轮追加，只增不改；MVP 中为代码层生成的 auto-pass 记录

    # --- Layer 2 / 2d（intelligence.py）写入 ---
    edl: list[EditDecision] = field(default_factory=list)
    # 最终剪辑决策表，由 segments_to_edl() 在 [a] 确认时编译生成
    human_feedback_history: list[HumanFeedbackRound] = field(default_factory=list)
    # 人工反馈历史，每次 [r] 追加，超过 N 轮时自动压缩旧轮次为摘要

    # --- Layer 2 循环控制（MVP 中固定值，字段存在保证 schema 兼容）---
    loop_metadata: LoopMetadata = field(default_factory=LoopMetadata)

    # --- runner.py 初始化填入，Layer 2 读取 ---
    goal: str = ""                        # 用户通过 --goal 指定的分析目标，LLM 相关性评分的基准

    # --- runner.py 维护 ---
    layer_completed: int = 0             # 0=初始化, 1=Layer1完成, 2=Layer2完成(含2d确认), 3=Layer3完成
    last_checkpoint: str = ""            # ISO 8601 时间戳，最后一次写入 JSON 的时刻
```

### 阶段间 I/O 契约：三文件格式

三层管道通过三个 JSON 文件交接，每个文件是对应阶段的输出，也是下一阶段的输入基线：

| 文件 | 写入方 | 读取方 | 说明 |
|------|--------|--------|------|
| `layer1_annotations.json`（JSON1） | Layer 1 · perception.py | Layer 2 · intelligence.py | 不可变真值源，贯穿全链路 |
| `layer2_input.json`（JSON2） | Layer 1 · perception.py | Layer 2 · intelligence.py（LLM Prompt） | 精简视图，去除 char_timestamps，供 LLM 消费 |
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
      "t_start": 0.0, "t_end": 15.3,
      "type": "speech",
      "content": "大家好，今天我们来聊深度学习。",
      "confidence": 0.91,
      "char_timestamps": [
        {"text": "大", "start": 0.00, "end": 0.18}
      ]
    },
    {
      "index": 1,
      "t_start": 15.3, "t_end": 17.1,
      "type": "silence",
      "content": null,
      "confidence": 1.0
    }
  ]
}
```

**约束：** `annotations[].index` 全局唯一且稳定，是 JSON2/JSON3 跨文件坐标系的基础。Layer 2 和 Layer 3 均通过 `index` 而非数组下标定位条目。

#### JSON2 — Layer 2 输入（`layer2_input.json`）

与 JSON1 等长，去除 `char_timestamps`（节约 LLM token），静音条目 `text=null, selectable=false`：

```json
{
  "source": "samples/video.mp4",
  "tokens": [
    {"index": 0, "type": "speech", "text": "大家好，今天我们来聊深度学习。", "selectable": true},
    {"index": 1, "type": "silence", "text": null, "selectable": false}
  ]
}
```

**约束：** `len(tokens) == len(JSON1.annotations)`，且 `tokens[i].index == annotations[i].index`。

#### JSON3 — Layer 2 输出（`layer2_output.json`）

LLM 输出的 keep_mask 数组，与人工 overrides 合并后写入此文件，供 Layer 3 消费：

```json
{
  "source": "samples/video.mp4",
  "keep_mask": [
    {"index": 0, "keep": true},
    {"index": 1, "keep": null}
  ]
}
```

**约束：** `len(keep_mask) == len(JSON1.annotations)`。`keep=true/false` 为 LLM 决策（speech 条目），`keep=null` 为静音条目（不由 LLM 决策）。Layer 3 的静音处理规则：两侧相邻 speech 条目均 `keep=true` 时，静音条目视为保留；否则删除。

#### 切点参数默认值

Layer 3 在从 keep_mask 编译时间区间时应用以下参数（可通过配置覆盖）：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `pre_pad` | 0.15s | 每个保留段起点向前扩展 |
| `post_pad` | 0.25s | 每个保留段终点向后扩展 |
| `min_duration` | 1.0s | 过短段合并到相邻段阈值 |

---

### 检查点目录结构

```
video_dir/
├── interview_final.mp4
└── .autosmartcut_interview_final/       ← 与视频同目录，携带视频名
    ├── manifest.json                    ← 当前最新完整清单
    ├── manifest.layer1.json             ← Layer 1 完成后快照
    ├── manifest.layer2.r0.json          ← Layer 2 第 0 轮快照（首次 2a Round 1 + Round 2 + 2b + 2c auto-pass）
    ├── manifest.layer2.r1.json          ← Layer 2 第 1 轮快照（用户 [r] 反馈后重跑）
    └── manifest.layer3.json             ← Layer 3 完成后快照
```

轮次 `r0` 是首次智能层运行（没有人工反馈），`r1` 起表示用户在 2d 提交 `[r]` 反馈后的重跑。每次重跑会整体替换 `comprehension`、`segments`、`review_reports`，并向 `human_feedback_history` 追加一条记录。

### 人工反馈历史压缩示例（N=1）

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
| Demo 1 | 识别层（Qwen3-ASR + 字级对齐 + 句级聚合） | 本轮规划并实现 |
| Demo 2 | 智能层（理解 → 决策 → 审核 → 人工） | 待独立规划 |
| Demo 3 | 执行层（smartcut 库集成） | 本轮规划并实现 |

智能层是最高风险和最不确定的环节，待识别层验证完成后单独设计。

### Demo 依赖关系

**关键路径：** Demo 1 → Demo 2（待设计）→ MVP

Demo 1 和 Demo 3 可并行，两者都不依赖对方的结论。Demo 1 和 Demo 3 均通过后，在进入 Demo 2 规划之前，可用硬编码 EDL 串联两个脚本做一次端到端冒烟确认（非正式 Demo，不单独建文件）。

---

### Demo 1：识别层 — Qwen3-ASR + 字级对齐 + 句级聚合

**文件：** `demos/demo1_asr.py`

**目的：** 验证 Qwen3-ASR + ForcedAligner 在中文视频上的实际可用性，确认三件事：

1. **Qwen3-ASR-1.7B 中文转写质量**：专有名词和同音字的误识率是否在 2a 符号表可消歧的范围内。
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
        "t_start": 0.0, "t_end": 15.3,
        "type": "speech",
        "content": "大家好，今天我们来聊深度学习。",
        "confidence": 0.91,
        "metadata": {
            "char_timestamps": [
                {"text": "大", "start": 0.00, "end": 0.18},
                {"text": "家", "start": 0.18, "end": 0.35}
            ]
        }
    },
    {"t_start": 15.3, "t_end": 17.1, "type": "silence", "content": "", "confidence": 1.0, "metadata": {}}
]
```

> **字段名说明：** `char_timestamps` 中的 `start`/`end` 是 perception 层从 `ForcedAlignItem.start_time`/`end_time` 归一化而来，单位为秒（float），与 `Annotation.t_start`/`t_end` 保持一致。

#### 验证什么

| 验证项 | 通过标准 |
|--------|----------|
| 转写质量 | 主要内容可辨认，同音字误识在 2a 符号表可消歧范围内 |
| char_timestamps 完整率 | > 95% 的汉字有有效时间戳（无空值、无缺失） |
| 字级对齐精度 | 随机抽样中位误差 < 200ms |
| 句级聚合边界 | 切分处对应自然句边界（人工目视确认 ≥ 90%） |
| 间隙静音推导覆盖 | > 90% 的明显停顿（≥ 0.8s）被捕捉 |
| 长音频行为 | 30 分钟以上音频是否需要分段处理 |
| vLLM spawn guard | Windows 下脚本正常启动，无子进程重入报错 |

#### Go/No-Go

- **Go：** char_timestamps 完整率 > 95%，字级误差 < 200ms → 确认 D3（Qwen3 组合）与 D5（字级间隙推导）可用于 MVP。
- **Pivot：** 完整率 ≤ 95% 但误差可接受 → 在句级聚合时对缺失字符做插值处理（均分相邻字间距），不阻塞 MVP。
- **Stop：** 字级对齐整体误差 > 500ms → 评估重新微调 ForcedAligner，或改用其他对齐方案。

#### 对后续决策的影响

- 确认 D3（模型组合）的实际可用性。
- char_timestamps 完整率结果决定 D16 句级聚合是否需要字符插值兜底逻辑。
- vLLM spawn guard 验证结果决定 Demo 2 和 MVP 的进程启动方式。

---

### Demo 2：智能层 — LLM 三轮调用 + CLI 审阅

**文件：** `demos/demo2_llm.py`

**覆盖子阶段：** 2a 理解（两轮）、2b 决策、2c auto-pass、2d CLI 人工审阅（含反馈循环）。

**当前状态：** 待 Demo 1 完成并确认识别层输出格式后实现。

**输入：** Demo 1 产出的标注 JSON（一段 5–10 分钟中文视频的 `annotations[]`）。

#### 2a Round 1 验证（bootstrap）

**Prompt 结构：**
- 系统提示：从原始 ASR 转写中提炼粗糙主旨，识别可能存在误识的专有名词/人名/术语
- 输入：`[时间范围] 原始转写文本` 列表
- 期望 JSON 输出：

```json
{
  "purpose": "...",
  "symbol_table": [
    {"term": "正确形式", "raw_form": "ASR误识形式", "category": "person|term|entity|other", "first_occurrence": 0}
  ]
}
```

**验证项：**
- JSON 可解析率（目标 10/10 次调用不崩溃）
- `symbol_table[]` 是否识别出视频中已知的专有名词
- `purpose` 是否概括了主要内容（人工目视）

#### 2a Round 2 验证（印证）

**Prompt 结构：**
- 在 Round 1 基础上追加 `purpose` 和 `symbol_table[]` 作为系统提示的一部分
- 期望 JSON 输出：

```json
{
  "purpose": "精化后的主旨",
  "checklist": [
    {"item": "...", "priority": "must|optional"}
  ],
  "cleaned_annotations": [
    {"annotation_index": 0, "cleaned_content": "..."}
  ]
}
```

**验证项：**
- `cleaned_annotations[]` 覆盖率（是否对含误识的 speech 标注都产出了消歧版本）
- `checklist[]` 的 must 项数量是否合理（通常 3–6 条）
- 消歧效果（对照 symbol_table，人工核查同音字是否被纠正）

#### 2b 验证（决策）

**Prompt 结构：**
- 输入：`cleaned_annotations[]`（消歧文本视图）+ `checklist[]`
- 期望 JSON 输出：`segments[]`（含每条 checklist 的 covered 状态）

**验证项：**
- segments 时间区间是否覆盖全部 annotations（无遗漏）
- `selected=True` 的片段是否覆盖所有 must 项
- 片段粒度：平均片段时长是否在 20s–3min 之间

#### 2d CLI 交互验证

- `[a]` 确认 → EDL 写入格式正确，`HumanFeedbackRound.verdict="confirm"` 追加
- `[t 1,3]` 切换 → `segments[].selected` 翻转，预计时长刷新
- `[r]` 反馈 → `HumanFeedbackRound.verdict="feedback"` 追加，触发 2a 重跑，第 2 轮起历史摘要正确构建

#### 验证什么

| 验证项 | 通过标准 |
|--------|----------|
| 三轮 JSON 可解析率 | 10/10 次不崩溃，格式合规 |
| segments 时间覆盖 | 无遗漏区间（全部 annotations 时间范围被覆盖）|
| must 项覆盖率 | ≥ 1 次调用中所有 must 项 covered=True |
| CLI 交互流畅度 | 三条路径（[a]/[t]/[r]）均可正常触发且状态正确 |
| 反馈历史压缩 | 第 2 轮 [r] 后，第 1 轮 feedback 被压缩为 summary |

#### Go/No-Go

- **Go：** 三轮调用 JSON 解析率 100%，segments 无时间遗漏，CLI 交互三路径正常 → 直接进入 MVP 工程化。
- **Pivot：** JSON 偶发格式错误 → 加入 retry + schema 验证兜底，不阻塞 MVP。
- **Stop：** `cleaned_annotations[]` 完全无效（消歧等于原文）→ 重新设计 Round 2 Prompt。

#### 对后续决策的影响

- 三轮 Prompt 结构在此处定形，直接复用到 MVP `intelligence.py`。
- 长文本分块策略（单块 60% 上下文窗口 + 2–3 段重叠）在此处实测，确认分块边界对 segment 合并的影响。

---

### Demo 3：执行层 — smartcut 库集成

**文件：** `demos/demo3_smartcut.py`

**目的：** 验证 `keep_mask → 时间区间 → smartcut` 完整链路，确认 Layer 3 在密集切点与真实 JSON1/JSON3 输入下均可正常工作。

Demo 有两种运行模式：`dense`（合成密集 EDL 压测）和 `json`（读取真实 JSON1+JSON3 走完整 keep_mask 管线）。

#### 做什么

**dense 模式（`python demos/demo3_smartcut.py dense --input samples/video.mp4`）：**
1. 合成密集 EDL（52 个 keep 段，每段 ~2.5 秒，keep/cut 交替）压测 smartcut 边界行为。
2. 不依赖 Layer 1/2 输出，聚焦 Layer 3 本身的极端条件。

**json 模式（`python demos/demo3_smartcut.py json --layer1 outputs/layer1_annotations.json --mask outputs/layer2_output.json`）：**
1. 读取 JSON1（`layer1_annotations.json`）获取 annotations 与视频路径。
2. 读取 JSON3（`layer2_output.json`）获取 keep_mask。
3. 调用 `execution.positive_segments_from_mask_files()` 走完整管线：keep_mask + silence 规则推导 → 时间区间 → padding → min_duration 合并 → `Fraction[]`。
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

- `execution.keep_mask_to_positive_segments()` 接口已锁定，直接用于 MVP `execution.py`。
- `pre_pad`/`post_pad`/`min_duration` 默认值已通过 json 模式实测，如需调整在此阶段确定。

---

> **【给后续 LLM / 协作者的提示】** 自下文「第 6 节」起至文档末尾的内容**尚未按当前方案更新**，**在删除本段提示之前不可作为参考或实现依据**；阅读与推理时**第 5 节可作为当前仓库结构参考，§6 及后续章节仍以重写后版本为准**。待 §6 及后续章节重写完成后，请删除本提示。

---

## 5. 仓库结构

```
AutoSmartCut/                           ← 新建独立仓库（D1）
├── demos/
│   ├── demo1_asr.py                    ← 识别层验证：Qwen3-ASR + 字级对齐 + 句级聚合
│   ├── demo2_llm.py                    ← 智能层验证：2a×2 + 2b×1 + 2c auto-pass + 2d CLI
│   ├── demo3_smartcut.py               ← smartcut 库集成验证
├── autosmartcut/
│   ├── __init__.py
│   ├── __main__.py                     ← CLI 入口（中文界面，含 Windows spawn guard）
│   ├── config.py                       ← 全局配置（LLM、checkpoint、max_raw_rounds 等）
│   ├── manifest.py                     ← 数据模型（第3节所有 dataclass）
│   ├── checkpoint.py                   ← 清单序列化 / 反序列化 / 恢复逻辑
│   ├── runner.py                       ← 管道编排器（串联三层 + 检查点 + 人工反馈重跑）
│   └── stages/
│       ├── __init__.py
│       ├── perception.py               ← Layer 1：直接调用 Qwen3-ASR，完成句级聚合与间隙静音推导
│       ├── intelligence.py             ← Layer 2：2a 理解、2b 决策、2c auto-pass、2d CLI 人工审阅
│       └── execution.py                ← Layer 3：edl → positive_segments → smart_cut()
├── pyproject.toml
├── README.md
├── AutoSmartCut.md                     ← 架构愿景文档
└── AutoSmartCut-MVP.md                 ← 本文档
```

**说明：**

- `demo0_smoke.py` 不单独建文件。第 4 节已明确，端到端冒烟只作为 Demo 1 与 Demo 3 通过后的临时串联验证。
- `demo4_cli_ux.py` 不存在。CLI 交互验证已包含在 `demo2_llm.py` 的 2d 人工审阅流程中。
- MVP 阶段不引入 `adapters/` 目录。ASR 只有 Qwen3-ASR 一种实现，直接在 `perception.py` 中调用；LLM 也只需在 `intelligence.py` 内封装当前使用的 OpenAI 兼容客户端，不做额外接口分离。
- 工作区中的 `Qwen3-ASR/` 以 editable install 方式引入（`pip install -e "./Qwen3-ASR[vllm]"`），是本仓库的外部依赖源，不属于 `AutoSmartCut/` 仓库内部目录结构。

> **【给后续 LLM / 协作者的提示】** 自下文「第 6 节」起至文档末尾的内容**尚未按当前方案更新**，**在删除本段提示之前不可作为参考或实现依据**。待 Demo 验证阶段结束、§6 及后续章节重写完成后，请删除本提示。

---

## 6. 各阶段实现细节

### Stage 1 — 识别层（Perception）

#### 音频提取

使用 PyAV 提取音频（通过 smartcut 间接引入的依赖，无需额外安装）：

```python
import av

def extract_audio(video_path: str, wav_path: str, sample_rate: int = 16000):
    """从视频文件提取单声道 WAV，供 ASR 使用"""
    with av.open(video_path) as container:
        audio_stream = container.streams.audio[0]
        resampler = av.AudioResampler(
            format='s16', layout='mono', rate=sample_rate
        )
        with av.open(wav_path, 'w') as out:
            out_stream = out.add_stream('pcm_s16le', rate=sample_rate)
            for frame in container.decode(audio_stream):
                for resampled in resampler.resample(frame):
                    for packet in out_stream.encode(resampled):
                        out.mux(packet)
```

#### ASR 适配器接口

```python
from typing import Protocol, TypedDict

class ASRResult(TypedDict):
    text: str
    start: float
    end: float
    confidence: float
    words: list[dict]   # 词级时间戳（可选，faster-whisper 支持）

class ASRAdapter(Protocol):
    def transcribe(self, audio_path: str, **kwargs) -> list[ASRResult]: ...

class FasterWhisperAdapter:
    """当前 MVP 实现：faster-whisper"""
    def __init__(self, model_size: str = "base", device: str = "auto"):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(model_size, device=device)

    def transcribe(self, audio_path: str, **kwargs) -> list[ASRResult]:
        segments, _ = self.model.transcribe(audio_path, language="zh", **kwargs)
        return [
            ASRResult(
                text=seg.text.strip(),
                start=seg.start,
                end=seg.end,
                confidence=seg.avg_logprob,  # 近似置信度
                words=[]
            )
            for seg in segments
        ]

# 未来适配器（接口不变）：
# class WhisperXAdapter:
#     def transcribe(self, audio_path: str, **kwargs) -> list[ASRResult]: ...
```

#### 静音推导逻辑（Demo 阶段）

```python
SILENCE_THRESHOLD_SECONDS = 0.8  # 可配置

def infer_silence_annotations(asr_results: list[ASRResult]) -> list[Annotation]:
    annotations = []
    for i, seg in enumerate(asr_results):
        annotations.append(Annotation(
            t_start=seg['start'], t_end=seg['end'],
            type="speech", content=seg['text'], confidence=seg['confidence']
        ))
        # 检查与下一段之间的间隙
        if i < len(asr_results) - 1:
            gap = asr_results[i + 1]['start'] - seg['end']
            if gap >= SILENCE_THRESHOLD_SECONDS:
                annotations.append(Annotation(
                    t_start=seg['end'], t_end=asr_results[i + 1]['start'],
                    type="silence", content="", confidence=1.0
                ))
    return annotations
```

---

### Stage 2 — 理解层（Comprehension）

#### LLM 适配器接口

```python
from typing import Protocol

class LLMAdapter(Protocol):
    def chat_json(self, messages: list[dict], **kwargs) -> dict: ...

class OpenAICompatibleAdapter:
    """兼容 OpenAI API 格式（覆盖 DeepSeek / Moonshot / Ollama 等）"""
    def __init__(self, base_url: str, api_key: str, model: str):
        from openai import OpenAI
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    def chat_json(self, messages: list[dict], **kwargs) -> dict:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            response_format={"type": "json_object"},
            **kwargs
        )
        import json
        return json.loads(response.choices[0].message.content)

# DeepSeek 配置示例：
# adapter = OpenAICompatibleAdapter(
#     base_url="https://api.deepseek.com",
#     api_key="sk-xxx",
#     model="deepseek-chat"
# )
```

#### Prompt 设计（MVP 版本）

```
系统提示：
你是一个视频内容分析助手。请分析以下视频转写内容，完成两个任务：
1. 将内容划分为语义连贯的片段，标注主题边界
2. 对每个片段评估与目标的相关性（0.0~1.0），判断是否建议保留

用户目标：{goal}

{如有历史反馈摘要：}
历史反馈摘要：
{history_summaries}

{如有最近轮次原文反馈：}
最近反馈（原文）：
{recent_feedback}

用户消息（转写内容）：
[00:00.0 - 00:15.3] 大家好，今天我们来聊一聊...
[00:15.3 - 02:41.0] 首先第一个话题是...
...

请以 JSON 格式输出（segments 数组）：
{
  "segments": [
    {
      "t_start": 0.0,
      "t_end": 15.3,
      "label": "开场白",
      "relevance": 0.3,
      "summary": "主播问好和频道介绍",
      "selected": false
    }
  ]
}
```

#### 长视频分块策略

- 按 token 数估算（每汉字约 1.5 token），单块不超过模型上下文窗口的 60%
- 相邻块之间保留 2-3 段重叠（约 200-300 字），避免主题边界被截断
- 分块结果合并时，重叠区间的 segment 取置信度较高的那份

---

### Stage 3 — 决策层（Decision）

#### CLI 审阅界面（中文，rich 渲染）

```
=== AutoSmartCut — 决策审阅 ===
源文件: interview_final.mp4（时长 00:15:32）
目标: 提取技术核心，目标5分钟
语义片段（共 12 个，LLM 建议保留 8 个，预计时长 04:52）

  编号  时间范围               主题         相关性  状态   摘要
   1   00:00.0 - 00:15.3   开场白          0.30   ---   主播问好和频道介绍
   2   00:15.3 - 02:41.0   核心话题一       0.90   ███   深度学习在视频处理的应用
   3   02:41.0 - 03:05.2   口误/重复        0.10   ---   说错后重新组织语言
   4   03:05.2 - 07:22.1   核心话题一（续）  0.88   ███   具体技术方案讲解
   5   07:22.1 - 07:55.0   [停顿]          0.00   ---
  ...

操作:
  [a]         确认当前选择，进入渲染
  [t 1,3]     切换片段的保留状态（逗号或空格分隔编号）
  [r]         输入反馈，重新分析（回到理解层）
  [s]         保存检查点
  [p]         预览当前选择的总时长
  [q]         退出
>
```

#### 三条交互路径

**路径 A — 确认通过 `[a]`：**
当前 segments 的 `selected=True` 部分转换为 EDL keep 动作，写入清单，进入 Stage 4。

**路径 B — 手动切换 `[t 编号]`：**
按编号切换对应 segment 的 `selected` 状态，立即刷新界面（显示新的预计时长），等待下一步操作。

**路径 C — 自然语言反馈 `[r]`：**
```
> r
请输入你的审阅意见: 开场白虽然是寒暄但包含了本期核心内容预告，应该保留

正在重新分析（第 2 轮）...
[Stage 2 重跑，feedback 写入 review_history，超过 N 轮时压缩摘要]
[刷新显示新的片段列表]
```

#### segments → edl 转换逻辑

```python
def segments_to_edl(segments: list[Segment]) -> list[EditDecision]:
    edl = [
        EditDecision(
            t_start=seg.t_start,
            t_end=seg.t_end,
            action="keep" if seg.selected else "cut"
        )
        for seg in segments
    ]
    return merge_adjacent_same_action(edl)  # 合并相邻同类决策，减少切点数量
```

---

### Stage 4 — 渲染层（Rendering）

#### EDL → smartcut 调用

```python
from fractions import Fraction
from smartcut.media_container import MediaContainer
from smartcut.smart_cut import smart_cut
from smartcut.misc_data import AudioExportInfo, AudioExportSettings

def edl_to_positive_segments(
    edl: list[EditDecision]
) -> list[tuple[Fraction, Fraction]]:
    """将剪辑决策表转换为 smartcut 所需的保留区间列表"""
    return [
        (
            Fraction(e.t_start).limit_denominator(1_000_000),
            Fraction(e.t_end).limit_denominator(1_000_000)
        )
        for e in edl if e.action == "keep"
    ]

def render(manifest: TimelineManifest, output_path: str):
    """Stage 4 核心：调用 smartcut 执行帧精确剪切"""
    positive_segments = edl_to_positive_segments(manifest.edl)
    if not positive_segments:
        raise ValueError("EDL 中没有 keep 片段，无法渲染")

    media = MediaContainer(manifest.source_media.path)
    audio_info = AudioExportInfo(
        output_tracks=[AudioExportSettings(codec='passthru')]
    )
    result = smart_cut(
        media_container=media,
        positive_segments=positive_segments,
        out_path=output_path,
        audio_export_info=audio_info
    )
    media.close()
    return result
```

**说明：** `MediaContainer` 在 Stage 4 单独构造，只用于 smartcut 的内部 GOP 分析，与清单中的 `source_media` 路径对应。Stage 0 构造的 `MediaContainer`（获取 duration 等元信息）已在当时关闭，不需要跨阶段保持连接。

---

## 7. CLI 命令设计

### 子命令结构

```bash
# 完整流程（Stage 1 → 2 → 3 → 4）
autosmartcut run input.mp4 -o output.mp4 --goal "提取技术核心，目标5分钟"

# 从检查点恢复
autosmartcut run input.mp4 -o output.mp4 --goal "..." --resume

# 只跑指定阶段（需要前置阶段的检查点）
autosmartcut run input.mp4 --stage 1        # 只跑识别层
autosmartcut run input.mp4 --stage 2        # 只跑理解层
autosmartcut run input.mp4 --stage 3        # 进入决策层 CLI 交互
autosmartcut run input.mp4 --stage 4        # 只跑渲染层（需要 --output）

# 非交互模式（跳过 Stage 3 人工审阅，直接使用 LLM 结果）
autosmartcut run input.mp4 -o output.mp4 --goal "..." --auto

# 查看当前清单状态
autosmartcut inspect input.mp4

# 配置管理
autosmartcut config set llm.api_key sk-xxx
autosmartcut config set llm.base_url https://api.deepseek.com
autosmartcut config set llm.model deepseek-chat
autosmartcut config set asr.model base
autosmartcut config set asr.language zh
autosmartcut config set review.max_raw_rounds 1
autosmartcut config get llm.model
autosmartcut config list
```

### `run` 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `input` | 输入视频文件路径（必填） | — |
| `-o / --output` | 输出视频文件路径（Stage 4 必填） | — |
| `--goal` | LLM 分析目标描述（Stage 2 必填） | — |
| `--resume` | 从上次检查点继续，跳过已完成阶段 | 否 |
| `--stage N` | 只执行第 N 阶段 | 全部 |
| `--auto` | 非交互模式，跳过 Stage 3 人工审阅 | 否 |

### CLI 恢复交互

当检测到检查点时：

```
检测到上次运行的检查点（Stage 1 已完成，2026-04-03 14:32:01）
  [c] 从 Stage 2 继续
  [1] 从 Stage 1 重跑
  [0] 全部重新开始
>
```

---

## 8. 依赖清单

```toml
[project]
name = "autosmartcut"
requires-python = ">=3.10"
dependencies = [
    "smartcut>=1.7",        # Stage 4 渲染引擎（含 PyAV，用于音频提取和视频剪切）
    "faster-whisper",       # Stage 1 ASR 引擎
    "openai",               # Stage 2 LLM 调用（兼容 DeepSeek 等 OpenAI 格式 API）
    "rich",                 # Stage 3 CLI 表格渲染（中文审阅界面）
    "tqdm",                 # 各阶段进度显示
]
```

**依赖说明：**
- PyAV 通过 smartcut 间接引入，音频提取也使用同一套 PyAV，不新增 FFmpeg 集成方式
- `openai` 包同时支持 OpenAI、DeepSeek、Moonshot、本地 Ollama 等兼容接口，只需配置 `base_url`
- `rich` 用于渲染 Stage 3 的中文审阅界面（表格、颜色高亮、交互输入）

---

## 9. MVP 之后的第一批扩展

以下功能**不在 MVP 范围**，但架构已预留空间，实现时不需要修改主干。

| 扩展项 | 影响的阶段 | 实现方式 |
|--------|-----------|----------|
| 字幕文件生成（SRT/ASS） | Stage 4 新增节点 | 基于 EDL 重新对齐 ASR 时间戳，输出字幕文件；smartcut 已有字幕轨 passthrough，可直接扩展 |
| WhisperX 切换 | Stage 1 适配器替换 | 新增 `WhisperXAdapter` 实现 `ASRAdapter` 协议；获得更精确的词级时间戳 + 说话人分离 |
| 声纹识别 / 说话人标注 | Stage 1 新增节点 | 通过 WhisperX 或独立说话人分离模型，在 annotations 中添加 `speaker_id` 字段 |
| GUI 审阅界面 | Stage 3 替换 CLI | 实现相同的 segments → edl 逻辑，只是交互层从 CLI 换为图形界面；时间轴清单结构不变 |
| 多剪辑策略预设（Preset） | Stage 3 新增模板 | 将不同目标（播客精华版、课程浓缩版等）封装为预设，自动填充 `--goal` 并调整 Stage 3 筛选规则 |
| 多文件批量处理 | 管道编排层 | `runner.py` 增加队列逻辑；每个文件独立检查点 |
| 图像识别 / 去广告 | Stage 1 新增节点 | 目标检测模型产出 `type: "ad"` 标注，Stage 3 默认将广告段设为 `selected=False` |
| Agent Loop 自动审阅 | Stage 5（新增） | vLLM 检查成片后生成修改建议，自动回到 Stage 3 迭代；人工只做最终确认 |

---

*文档版本：0.1.0*
*记录日期：2026-04-03*
*对应架构愿景：[AutoSmartCut.md](AutoSmartCut.md)*
