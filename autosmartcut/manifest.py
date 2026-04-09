"""TimelineManifest 及所有 dataclass 定义。

贯穿全管道的唯一共享状态，各层只读/写此结构，由 runner.py 串联。
设计保证：每层只追加自己负责的字段，不覆写上游字段（append-only）。
可完整序列化为 JSON，支持断点恢复和审计追溯。
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 源媒体引用 — runner.py 初始化时填入
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
# Layer 2 / 2a 理解层产出 — comprehension 的子结构
# ---------------------------------------------------------------------------

@dataclass
class ChecklistItem:
    """comprehension.checklist[] 的单条条目。"""
    item: str
    priority: str           # "must" | "optional"
    covered: bool = False   # 2b 决策时由 LLM 填写；完整版 2c 审核时独立验证


@dataclass
class SymbolTableEntry:
    """comprehension.symbol_table[] 的单条条目。
    2a Round 1（bootstrap）产出，记录 ASR 可能的误识形式与正确形式的对应关系。
    固化字段：Round 0 写入后不再重算，持久化仅供审计，不向 2b/2c 传递。"""
    term: str               # 正确形式（如"张伟"）
    raw_form: str           # ASR 可能的误识形式（如"章维"）
    category: str           # "person" | "term" | "entity" | "other"
    first_occurrence: int   # 首次出现的 annotation_index（0-based）


@dataclass
class CleanedAnnotation:
    """comprehension.cleaned_annotations[] 的单条条目。
    2a Round 2（印证）产出，在不修改原始 annotations[].content 的前提下追加消歧文本。
    固化字段：Round 0 写入后不再重算，遵循 Append-only。
    2b/2c 消费此字段而非原始 annotations[].content。"""
    annotation_index: int   # 对应 annotations[].index（0-based）
    cleaned_content: str    # 消歧后的文本（同音字纠正、专有名词还原等）


@dataclass
class Comprehension:
    """Layer 2 / 2a 理解子阶段产出（固定两轮：Round 1 bootstrap + Round 2 印证）。
    存储对内容的整体语义理解，是 2b 决策和 2c 审核的评估基准。

    字段稳定性：
      固化（Round 0 后不变）：symbol_table, cleaned_annotations
      可变（每轮重跑可更新）：purpose, checklist
    """
    purpose: str
    checklist: list[ChecklistItem] = field(default_factory=list)
    symbol_table: list[SymbolTableEntry] = field(default_factory=list)           # Round 1 产出，固化
    cleaned_annotations: list[CleanedAnnotation] = field(default_factory=list)   # Round 2 产出，固化
    content_map: list = field(default_factory=list)   # 叙事图谱，中期扩展预留，MVP 置空


# ---------------------------------------------------------------------------
# Layer 2 / 2b 决策层产出
# ---------------------------------------------------------------------------

@dataclass
class KeepMaskEntry:
    """segments[].keep_mask[] 的单条条目。
    LLM 只输出布尔决策，不输出时间戳——时间戳由 Layer 3 从 JSON1 反查。"""
    index: int          # 对应 JSON1 annotations[].index，跨文件对齐坐标
    keep: bool          # True=保留, False=删除


@dataclass
class Segment:
    """Layer 2 / 2b 决策子阶段写入：LLM 决策记录，按 keep_mask 条目组织。

    [t] 操作将变更追加到当前 HumanFeedbackRound.overrides（delta 模式，不原地修改 keep_mask）。
    最终有效决策 = keep_mask + 累积 overrides 合并推导（AI 决策叠加人工 delta）。
    发送给 LLM 重跑时，将 keep_mask 与所有 overrides 合并后作为上一轮决策基线传入。"""
    keep_mask: list[KeepMaskEntry] = field(default_factory=list)
    # 当前轮 LLM 输出的完整决策数组，长度等于 JSON1 annotations 总数
    label_map: dict[int, str] = field(default_factory=dict)
    # index → 主题标签，供 CLI 审阅界面展示（可选，LLM 顺带输出）
    summary_map: dict[int, str] = field(default_factory=dict)
    # index → 内容摘要，供 CLI 审阅界面展示（可选，LLM 顺带输出）


# ---------------------------------------------------------------------------
# Layer 2 / 2c 审核层产出
# ---------------------------------------------------------------------------

@dataclass
class ReviewReport:
    """Layer 2 / 2c 审核子阶段产出。每轮追加，只增不改。
    MVP 中由代码层自动生成（verdict 固定为 "pass"，coverage_issues 为空列表）。
    字段结构与完整版保持一致，未来启用真实 LLM 审核时只修改生成逻辑，不改 schema。"""
    round: int
    verdict: str                             # "pass" | "fix_decision" | "fix_checklist"
    coverage_issues: list[str] = field(default_factory=list)
    completeness_issues: list[str] = field(default_factory=list)
    token_spent: int = 0


# ---------------------------------------------------------------------------
# Layer 2 / 2d 人工层产出
# ---------------------------------------------------------------------------

@dataclass
class EditDecision:
    """最终剪辑决策，由 segments_to_edl() 在 [a] 确认时编译生成。
    Layer 3 只读此表，不再回溯 segments[]。
    edl[] 是唯一写入点：merge(keep_mask + 所有 overrides) → 编译为此列表。"""
    t_start: float
    t_end: float
    action: str         # "keep" | "cut"


@dataclass
class HumanFeedbackRound:
    """每次 [r] 反馈或 [a] 确认触发追加一条，只增不改。
    超过 N 轮时旧轮次的 feedback 置空并压缩进 summary，
    控制发给 LLM 的上下文长度（N 由 config.intelligence.max_raw_rounds 配置）。"""
    round: int
    verdict: str        # "confirm"（[a] 确认通过）| "feedback"（[r] 自然语言反馈）
    overrides: list[dict] = field(default_factory=list)
    # [t] 操作产生的 delta 记录：[{"index": int, "keep": bool}]
    # 最终有效决策 = keep_mask + 所有 overrides 合并推导（later 覆盖 earlier）
    feedback: str = ""  # 原文反馈（最近 N 轮保留，更早的置为空字符串；verdict=confirm 时为空）
    summary: str = ""   # 压缩摘要（超过 N 轮后填入，否则为空字符串）
    timestamp: str = "" # ISO 8601 格式


# ---------------------------------------------------------------------------
# Layer 2 循环控制元数据
# ---------------------------------------------------------------------------

@dataclass
class LoopMetadata:
    """Layer 2 循环控制元数据。
    MVP 中 inner_rounds=outer_rounds=0（循环禁用），final_verdict 固定为 "pass"。
    字段存在只为保证 schema 与完整版兼容。"""
    total_token_spent: int = 0
    inner_rounds: int = 0    # fix_decision 内循环执行次数
    outer_rounds: int = 0    # fix_checklist 外循环执行次数
    final_verdict: str = ""  # "pass" | "budget_exceeded" | "max_rounds"


# ---------------------------------------------------------------------------
# 顶层：TimelineManifest — 贯穿全管道的唯一共享状态
# ---------------------------------------------------------------------------

@dataclass
class TimelineManifest:
    """贯穿全管道的唯一共享状态。各层只读/写此结构，由 runner.py 串联。
    设计保证：每层只追加自己负责的字段，不覆写上游字段（append-only）。
    可完整序列化为 JSON，支持断点恢复和审计追溯。"""

    # --- runner.py 初始化时填入 ---
    version: str            # schema 版本，用于未来格式迁移兼容性判断，如 "0.1.0"
    source_media: SourceMedia

    # --- Layer 1（perception.py）写入 ---
    annotations: list[Annotation] = field(default_factory=list)
    # 识别层产出：句级标注，含 t_start/t_end、gap_after 与 ASR 置信度

    # --- Layer 2 / 2a（intelligence.py）写入，固定两轮 ---
    comprehension: Comprehension | None = None
    # 理解子阶段产出：主旨 + 检查清单 + 符号表 + 消歧标注
    segments: list[Segment] = field(default_factory=list)
    # 决策子阶段产出（2b 独立一次 LLM 调用）；每轮人工 [r] 反馈后整体替换

    # --- Layer 2 / 2c（intelligence.py）写入 ---
    review_reports: list[ReviewReport] = field(default_factory=list)
    # 审核报告列表：每轮追加，只增不改；MVP 中为代码层生成的 auto-pass 记录

    # --- Layer 2 / 2d（intelligence.py）写入 ---
    edl: list[EditDecision] = field(default_factory=list)
    # 最终剪辑决策表，由 segments_to_edl() 在 [a] 确认时编译生成（唯一写入点）
    human_feedback_history: list[HumanFeedbackRound] = field(default_factory=list)
    # 人工反馈历史：每次 [r] 追加，超过 N 轮时自动压缩旧轮次为摘要

    # --- Layer 2 循环控制（MVP 中固定值，字段存在保证 schema 兼容）---
    loop_metadata: LoopMetadata = field(default_factory=LoopMetadata)

    # --- runner.py 初始化填入，Layer 2 读取 ---
    goal: str = ""          # 用户通过 --goal 指定的分析目标，LLM 相关性评分的基准

    # --- runner.py 维护 ---
    layer_completed: int = 0    # 0=初始化, 1=Layer1完成, 2=Layer2完成(含2d确认), 3=Layer3完成
    last_checkpoint: str = ""   # ISO 8601 时间戳，最后一次写入 JSON 的时刻
