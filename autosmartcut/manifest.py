"""TimelineManifest 及相关 dataclass 定义（类型与愿景对齐用）。

**运行时事实（MVP 现行）**
- Layer 2 智能层在内存中使用 **dict** 承载 `annotations`、`comprehension`、`keep_mask` 等
  （见 ``intelligence.run_intelligence_layer``），避免与 dataclass 来回转换。
- 本文件中的 dataclass 保留作 **schema 草图 / 完整版叙事对齐**；字段与 dict 不一致时，
  **以 ``intelligence-layer2-mvp.md`` 与 ``intelligence*.py`` 中的 dict 契约为准**。
- 统一编排入口 ``runner.py`` 尚未接入上述 dict 全链路；检查点目录约定见 ``doc/AutoSmartCut-MVP.md``（规划中）。

贯穿全管道的设计目标：各层只追加自己负责的字段、不覆写上游（append-only），
清单可序列化为 JSON，供未来检查点与审计使用。
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 源媒体引用 — runner 初始化时填入（完整版叙事）
# ---------------------------------------------------------------------------

@dataclass
class SourceMedia:
    path: str                    # 相对或绝对路径
    duration: float              # 总时长（秒）
    audio_track_index: int = 0   # 用于 ASR 的音轨索引


# ---------------------------------------------------------------------------
# Layer 1（perception.py）写入
# ---------------------------------------------------------------------------

@dataclass
class Annotation:
    """Layer 1 句级 ASR 与对齐产出的原始事实记录，不承载语义判断。"""
    index: int          # 全局唯一序号（0-based），JSON1/JSON2/JSON3 跨文件对齐的坐标
    t_start: float
    t_end: float
    content: str        # 句级转写文字
    confidence: float   # Qwen3-ASR 产出的置信度信号，仅作为模型可靠性参考，不用于 UI 显示
    gap_after: float = 0.0   # 至下一句起点或媒体结尾的间隔（秒）
    metadata: dict = field(default_factory=dict)
    # 开放式扩展字段；speech 条目含：
    #   char_timestamps: [{"text": str, "start": float, "end": float}]
    #   由 Qwen3-ForcedAligner 产出，是精确切点、精确字幕和语气词剪除的基础
    #   注意：ForcedAlignItem 原始字段名为 start_time/end_time，
    #   perception 层归一化为 start/end（秒）后写入此处


# ---------------------------------------------------------------------------
# Layer 2 / 2a — 完整版叙事中的 comprehension 子结构（MVP 运行时见 dict）
# ---------------------------------------------------------------------------

@dataclass
class ChecklistItem:
    """comprehension.checklist[] 的单条条目（完整版 2a/2c）。"""
    item: str
    priority: str           # "must" | "optional"
    covered: bool = False   # 2b 决策时由 LLM 填写；完整版 2c 审核时独立验证


@dataclass
class SymbolTableEntry:
    """comprehension.symbol_table[] 的单条条目（完整版 2a Round 1）。"""
    term: str               # 正确形式（如"张伟"）
    raw_form: str           # ASR 可能的误识形式（如"章维"）
    category: str           # "person" | "term" | "entity" | "other"
    first_occurrence: int   # 首次出现的 annotation_index（0-based）


@dataclass
class CleanedAnnotation:
    """comprehension.cleaned_annotations[] 的单条条目（MVP：程序按 R2 corrections 生成）。"""
    annotation_index: int   # 对应 annotations[].index（0-based）
    cleaned_content: str    # 消歧后的文本（同音字纠正、专有名词还原等）


@dataclass
class Comprehension:
    """Layer 2 / 2a 理解子阶段（完整版字段草图）。

    **MVP 运行时** dict 仅持久化 ``purpose``、``outline_blocks``、``cleaned_annotations``
    （稠密全量），见 ``intelligence_2a.py`` 与 ``intelligence-layer2-mvp.md`` §4–§5。
    下列 ``checklist`` / ``symbol_table`` 为完整版预留，勿与现行 Layer2 dict 强行等同。
    """
    purpose: str
    checklist: list[ChecklistItem] = field(default_factory=list)
    symbol_table: list[SymbolTableEntry] = field(default_factory=list)
    cleaned_annotations: list[CleanedAnnotation] = field(default_factory=list)
    content_map: list = field(default_factory=list)   # 叙事图谱，中期扩展预留，MVP 置空


# ---------------------------------------------------------------------------
# Layer 2 / 2b 决策层产出
# ---------------------------------------------------------------------------

@dataclass
class KeepMaskEntry:
    """单句级条目的 keep/cut 决策。

    **MVP**：与 JSON3、以及运行中 ``manifest_dict["keep_mask"]`` 逐项对应；
    时间由 Layer 1 的 ``t_start``/``t_end``/``gap_after`` 与 Layer 3 合并，不由 LLM 输出。
    """
    index: int          # 对应 JSON1 annotations[].index，跨文件对齐坐标
    keep: bool          # True=保留, False=删除


@dataclass
class Segment:
    """完整版叙事：决策子阶段可按片段组织；MVP 以顶层 keep_mask（dict）为主。

    人工 [t] 的 delta 记在 ``HumanFeedbackRound.overrides``；确认后与 keep_mask 合并，
    见 ``intelligence_2d.py``。
    """
    keep_mask: list[KeepMaskEntry] = field(default_factory=list)
    label_map: dict[int, str] = field(default_factory=dict)
    summary_map: dict[int, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Layer 2 / 2c 审核层产出
# ---------------------------------------------------------------------------

@dataclass
class ReviewReport:
    """Layer 2 / 2c 审核子阶段产出。每轮追加，只增不改。

    MVP 占位实现可写入单条 ``review_report``（dict）；完整版为 ``review_reports[]`` 列表。
    """
    round: int
    verdict: str                             # "pass" | "fix_decision" | "fix_checklist"
    coverage_issues: list[str] = field(default_factory=list)
    completeness_issues: list[str] = field(default_factory=list)
    token_spent: int = 0


# ---------------------------------------------------------------------------
# Layer 2 / 2d 人工层与「EDL」叙事
# ---------------------------------------------------------------------------

@dataclass
class EditDecision:
    """时间轴上的 keep/cut 等决策（完整版清单内 ``edl[]`` 叙事）。

    **MVP 现行**：智能层对外只交付 **定稿 ``keep_mask``**（JSON3）；执行层读取 JSON1+JSON3，
    在 **``execution.py`` 内部** 将连续保留句合并为时间区间，再转为 ``Fraction`` 传入
    smartcut。**不在** MVP 路径上要求清单或 Layer2 落盘本结构。

    本 dataclass 保留给完整版（2d 确认后写入清单 ``edl[]`` 再由 Layer3 消费）或将来工具链导出。
    """
    t_start: float
    t_end: float
    action: str         # "keep" | "cut"


@dataclass
class HumanFeedbackRound:
    """人工子阶段记录（完整版含 [r] 自然语言反馈等多路径）。

    **MVP 现行**（``intelligence-layer2-mvp.md`` §8）：仅确认与 index 级 overrides，
    无自然语言反馈回流 2a/2b。
    """
    round: int
    verdict: str        # "confirm" | "feedback"（完整版）；MVP 多为 "confirm"
    overrides: list[dict] = field(default_factory=list)
    # [{"index": int, "keep": bool}]；later 覆盖 earlier
    feedback: str = ""
    summary: str = ""
    timestamp: str = ""


# ---------------------------------------------------------------------------
# Layer 2 循环控制元数据
# ---------------------------------------------------------------------------

@dataclass
class LoopMetadata:
    """智能层循环控制（完整版）。MVP 禁用循环时多为零值与 pass。"""
    total_token_spent: int = 0
    inner_rounds: int = 0
    outer_rounds: int = 0
    final_verdict: str = ""  # "pass" | "budget_exceeded" | "max_rounds"


# ---------------------------------------------------------------------------
# 顶层：TimelineManifest（完整版叙事；MVP 全链路 runner 未接）
# ---------------------------------------------------------------------------

@dataclass
class TimelineManifest:
    """贯穿全管道的单一状态快照（完整版目标形态）。

    **MVP 现行**：Layer2 工作数据为 dict；本结构未与 runner/checkpoint 串联。
    **MVP 清单内不设顶层 ``keep_mask`` 字段时**：以运行中 dict / JSON3 的 ``keep_mask`` 为准。
    """

    version: str
    source_media: SourceMedia

    annotations: list[Annotation] = field(default_factory=list)

    comprehension: Comprehension | None = None
    segments: list[Segment] = field(default_factory=list)

    review_reports: list[ReviewReport] = field(default_factory=list)

    edl: list[EditDecision] = field(default_factory=list)
    # 完整版：2d 确认后可写入。MVP：执行层不依赖清单中的 edl，见 EditDecision 说明。

    human_feedback_history: list[HumanFeedbackRound] = field(default_factory=list)

    loop_metadata: LoopMetadata = field(default_factory=LoopMetadata)

    goal: str = ""

    layer_completed: int = 0
    last_checkpoint: str = ""
