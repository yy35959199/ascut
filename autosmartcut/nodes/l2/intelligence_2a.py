"""Layer 2 / 2a 理解子阶段（MVP 契约见 doc/intelligence-layer2-mvp.md §5）

## 流程
1. **tokens**：manifest 中 ``tokens[]``，每项仅 ``index`` + ``text``（JSON2 句面）。
2. **R1（LLM，仅内存）**：`purpose_rough`、`outline_blocks_rough`、
   `candidate_misrecognitions`。
3. **R2（LLM，仅内存）**：`purpose`、`outline_blocks`、`corrections`
   （唯一替换列表，nth 1-based）。
4. **程序**：按 `corrections` 在 **只读句面**（``tokens[].text``）上替换，生成稠密
   `cleaned_annotations[]`；**不**改写 ``tokens``（Append-only）。

LLM 调用封装：R1/R2 均通过 ``call_structured`` + ``build_messages`` / ``prepare_next_turn_messages``（真多轮，前缀与 R1 共享以利缓存）。

## 输入（manifest 片段）
- `tokens[]`：句级句面（index、text）。
- `goal`：可选。

## 输出（写入 manifest）
manifest_dict["comprehension"] = {
    "purpose": str,
    "outline_blocks": [{"start_index", "end_index", "summary"}, ...],
    "cleaned_annotations": [{"annotation_index", "cleaned_content"}, ...],  # 稠密；程序生成
}

默认不在本模块内写盘；若调用方传入 ``on_phase_save``，由编排层将 R1/R2 检查点写入
``timeline_manifest.json`` 的 ``current.l2_checkpoints``（见 ``manifest_io.write_l2_checkpoint``）。
不写入 symbol_table。

## 注意
- cleaned_annotations 为稠密序列，长度与 tokens 一致（字段名沿用 MVP ``annotation_index``）。
"""

import copy
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType

from autosmartcut.nodes.l2.intelligence_llm import (
    StructuredResult,
    build_messages,
    call_structured,
    prepare_next_turn_messages,
)
from autosmartcut.nodes.l2.annotation_tokens import validate_tokens

logger = logging.getLogger(__name__)

# ============================================================================
# 纠错数据结构
# ============================================================================

@dataclass
class Correction:
    """单条纠错规则（对应 R2 输出的单条 correction）"""
    nth: int    # 该子串在原句中从左到右第几次出现（1-based）
    old: str    # 原始错误子串
    new: str    # 替换为


# ============================================================================
# 主入口
# ============================================================================

def run_2a_comprehension(
    manifest_dict: dict,
    *,
    on_phase_save: Callable[[str, dict], None] | None = None,
    on_chunk: Callable | None = None,
) -> dict:
    """2a 理解子阶段：两轮 LLM + 稀疏纠错 + 稠密回填

    Args:
        manifest_dict: 包含 ``tokens``（JSON2 句面）、``goal``、可选 ``source`` 的工作数据
        on_phase_save: 可选；``("2a_r1", payload)`` 在 R1 完成后、``("2a_r2", payload)`` 在
            稠密 ``comprehension`` 写入 ``manifest_dict`` 之后调用，用于将 TimelineManifest 落盘。
        on_chunk: 可选；透传给 ``call_structured``，每个流式 StreamChunk 事件调用一次。

    Returns:
        追加了 comprehension 字段的 manifest_dict
    """
    logger.info("[2a] 理解子阶段开始")

    # 句面列表（内存稠密），与清单 annotations 等长；供 2a 提示词与 LLM 使用
    tokens = manifest_dict["tokens"]
    # 校验 index 与下标一致，避免后续 prompt / 程序步骤错位
    validate_tokens(tokens)
    # 用户剪辑目标，原样进入 R1/R2 提示词首行「用户目标：…」
    goal = manifest_dict.get("goal", "")

    # Round 1：内部会 `_build_r1_prompt` + `call_structured(..., "r1")`（见 intelligence_llm）
    purpose_rough, outline_blocks_rough, candidate_misrecognitions, r1_completion = (
        _run_round1(tokens, goal, on_chunk=on_chunk)
    )
    if on_phase_save is not None:
        on_phase_save(
            "2a_r1",
            {
                "purpose_rough": purpose_rough,
                "outline_blocks_rough": copy.deepcopy(outline_blocks_rough),
                "candidate_misrecognitions": copy.deepcopy(
                    candidate_misrecognitions
                ),
            },
        )

    # Round 2：`prepare_next_turn_messages` 接上 R1 的 assistant JSON，再 `call_structured(..., "r2")` 追加 schema
    purpose, outline_blocks, raw_corrections = _run_round2(
        tokens,
        goal,
        candidate_misrecognitions,
        r1_completion,
        on_chunk=on_chunk,
    )

    # 程序步骤：只读句面为 tokens[].text，不修改 tokens
    token_text_view = MappingProxyType(
        {int(t["index"]): str(t.get("text", "")) for t in tokens}
    )

    sparse_cleaned_annotations = _build_sparse_cleaned_annotations(
        token_text_view, raw_corrections
    )
    cleaned_annotations = _densify_cleaned_annotations_from_tokens(
        tokens, sparse_cleaned_annotations
    )

    manifest_dict["comprehension"] = {
        "purpose": purpose,
        "outline_blocks": outline_blocks,
        "cleaned_annotations": cleaned_annotations,
    }
    if on_phase_save is not None:
        on_phase_save(
            "2a_r2",
            {"comprehension": copy.deepcopy(manifest_dict["comprehension"])},
        )

    logger.info("[2a] 完成 - 主旨: %s...", purpose[:60])
    logger.info("[2a] 消歧标注(稠密): %d 条", len(cleaned_annotations))
    logger.info("[2a] 分块: %d 块", len(outline_blocks))

    return manifest_dict


# ============================================================================
# Round 1: 粗理解 + ASR 误识候选
# ============================================================================

def _run_round1(
    tokens: list[dict],
    goal: str,
    *,
    on_chunk: Callable | None = None,
) -> tuple[str, list[dict], list[dict], StructuredResult]:
    """Round 1: 粗理解 + ASR 误识候选

    Returns:
        (purpose_rough, outline_blocks_rough, candidate_misrecognitions, r1_completion)
    """
    # 控制台阶段标记，便于对照日志与 LLM 调用
    logger.info("[2a-R1] 粗理解与误识候选构建")

    # 纯文本 user 主体（不含 JSON Schema 示例；示例由 intelligence_llm._build_messages 追加）
    prompt = _build_r1_prompt(tokens, goal)
    # R1 输出结构的 jsonschema，用于生成「示例 JSON」尾缀 + 校验模型返回
    schema = _get_r1_schema()

    # intelligence_llm.call_structured（stage=r1）：
    # - 内部 build_messages → system=SYSTEM_PROMPT，user=prompt+JSON 示例+纪律说明
    # - 失败按 max_retries 重试
    # - 返回 StructuredResult：含解析后 dict、assistant 原文 JSON、请求快照（供 R2 多轮前缀）
    r1_completion = call_structured(build_messages(prompt, schema), schema, "r1", on_chunk=on_chunk)
    # 已通过 jsonschema 校验的结构化对象（可直接按键取值）
    response = r1_completion.data

    # 必填主旨（schema required）
    purpose_rough = response["purpose_rough"]
    # 粗分块可选；缺省按空列表处理，避免下游 KeyError
    outline_blocks_rough = response.get("outline_blocks_rough", [])
    # 误识候选必填列表（可为空数组）
    candidate_misrecognitions = response.get("candidate_misrecognitions", [])

    # 日志截断显示，避免终端被长文本刷屏
    logger.info("[2a-R1] 粗糙主旨: %s...", purpose_rough[:60])
    logger.info("[2a-R1] 误识候选: %d 条", len(candidate_misrecognitions))

    # r1_completion 整块交给 R2 以复用 OpenAI 式 messages 前缀
    return purpose_rough, outline_blocks_rough, candidate_misrecognitions, r1_completion


def _build_r1_prompt(tokens: list[dict], goal: str) -> str:
    # 仅保留非空 text 的句行，格式 "[idx] 文本"，与 LLM 约定一致
    lines = [f"[{t['index']}] {t['text']}" for t in tokens if t.get("text", "").strip()]
    # 拼成单一大段 ASR 正文，插入 prompt 中部
    text_block = "\n".join(lines)
    # 首行：有 goal 则照抄；无则给默认剪辑意图说明
    goal_line = f"用户目标：{goal}" if goal else "用户目标：无特定目标，提取核心内容"

    # 以下为 R1 user 正文；末尾「请以 JSON…」之后 intelligence_llm 会再拼「示例 JSON + Schema 尾缀」
    return f"""{goal_line}

【阶段定位】当前阶段：2a 理解层 · 第 1 轮（R1）。本轮任务：从原始 ASR 转写中提炼粗糙主旨（purpose_rough）、按主题初步分块（outline_blocks_rough）、列出可能被误识的专有名词/术语候选（candidate_misrecognitions）。纠错与精化主旨在下一轮 R2 完成；本轮不要输出 corrections。

以下是视频 ASR 转写文本（格式：[index] 文字内容）：

{text_block}

请完成以下任务：
1. 用 1-2 句话描述内容的核心论点与叙事意图（purpose_rough）：
   - 重点是“说话者要论证/传达什么”，而非仅描述“讲了什么话题”
   - 若内容有明显的先后论述顺序，需适度体现（如“先…再…最后…”）
2. 将内容按主题/段落初步划分为若干块（outline_blocks_rough）：**每个元素**必须是 JSON 对象，且**仅**包含三个键——整数 `start_index`、整数 `end_index`、字符串 `topic`（表示该块覆盖的句级 index 闭区间与块主题）。**禁止**使用 `index_range` 或其它键名表示区间。
3. 识别可能被 ASR 误识的专有名词、人名、术语，给出候选正确形式

candidate_misrecognitions 中每条包含：
- annotation_index：发生误识的句子 index
- wrong：ASR 识别出的错误子串（必须是该句子的真实子串）
- suggestions：候选正确形式列表（通常 1 个；仅在语义上难以分辨时给 2-3 个）

请以 JSON 格式输出。"""


def _get_r1_schema() -> dict:
    """R1 的 JSON Schema：与 `_build_r1_prompt` 任务字段一一对应；供 intelligence_llm 生成示例尾缀 + 校验。"""
    return {
        "type": "object",  # 根必须为 JSON 对象（json_object 模式）
        "additionalProperties": False,
        "properties": {  # 允许的顶层键集合
            "purpose_rough": {  # 任务 1：粗糙主旨
                "type": "string",
                "description": "内容核心主旨（粗糙版，1-2句话）",
            },
            "outline_blocks_rough": {  # 任务 2：粗分块（数组元素形状见 items）
                "type": "array",
                "description": "初步内容分块",
                "items": {  # 每一块：闭区间 [start_index,end_index] + 主题词 topic
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "start_index": {"type": "integer"},  # 与 tokens 的 index 同坐标系
                        "end_index": {"type": "integer"},
                        "topic": {"type": "string", "description": "块主题词"},
                    },
                    "required": ["start_index", "end_index", "topic"],  # 缺任一即校验失败
                },
            },
            "candidate_misrecognitions": {  # 任务 3：误识候选（可为空数组）
                "type": "array",
                "description": "ASR 可能误识的词条候选列表",
                "items": {  # 每条候选绑定到句级 index
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "annotation_index": {
                            "type": "integer",
                            "description": "发生误识的句子 index",
                        },
                        "wrong": {
                            "type": "string",
                            "description": "ASR 识别出的错误子串",
                        },
                        "suggestions": {  # R2 纠错时 new 必须来自此列表
                            "type": "array",
                            "description": "候选正确形式（通常 1 个）",
                            "items": {"type": "string"},  # 字符串建议列表
                        },
                    },
                    "required": ["annotation_index", "wrong", "suggestions"],
                },
            },
        },
        "required": ["purpose_rough", "candidate_misrecognitions"],  # 粗分块非 required，允许模型省略
    }


# ============================================================================
# Round 2: 精化主旨 + 分块 + 纠错列表
# ============================================================================

def _run_round2(
    tokens: list[dict],
    goal: str,
    candidate_misrecognitions: list[dict],
    r1_completion: StructuredResult,
    *,
    on_chunk: Callable | None = None,
) -> tuple[str, list[dict], list[dict]]:
    """Round 2: 精化主旨 + 分块 + 纠错列表（多轮第二跳）

    Returns:
        (purpose, outline_blocks, raw_corrections)
        raw_corrections: [{"index": int, "old": str, "nth": int, "new": str}, ...]
    """
    logger.info("[2a-R2] 精化理解与纠错确定")

    # R2 的「新增 user」纯文本：承接 R1 JSON，不在此重复粘贴 R1 全文字段
    r2_user = _build_r2_user_followup(tokens, goal, candidate_misrecognitions)
    # R2 与 R1 不同 schema（精化 purpose、outline_blocks、corrections）
    schema = _get_r2_schema()

    # intelligence_llm.prepare_next_turn_messages：
    # - 拷贝 R1 请求快照 + 追加 assistant=R1 原始 JSON 字符串 + 追加 user=r2_user
    # - 供 API 形成「同一对话前缀」以利缓存与多轮契约
    r2_messages = prepare_next_turn_messages(
        r1_completion.request_messages,  # R1 的 system+user（含 R1 任务与 JSON 尾缀）
        assistant_content=r1_completion.assistant_content,  # R1 模型返回的 JSON 原文
        next_user_content=r2_user,  # 本函数构造的第二轮 user 任务说明
    )

    # intelligence_llm.call_structured（stage=r2）：
    # - 在**最后一条** user 末尾再拼 R2 的 JSON 示例 + Schema 说明
    response = call_structured(r2_messages, schema, "r2", on_chunk=on_chunk).data

    # R2 精化主旨（schema required）
    purpose = response["purpose"]
    # 精化分块列表（required；get 防御性一致化）
    outline_blocks = response.get("outline_blocks", [])
    # 稀疏纠错规则；可为空数组表示模型未发现可确认误识
    raw_corrections = response.get("corrections", [])

    logger.info("[2a-R2] 精化主旨: %s...", purpose[:60])
    logger.info(
        "[2a-R2] 分块: %d 块，纠错: %d 条",
        len(outline_blocks),
        len(raw_corrections),
    )

    return purpose, outline_blocks, raw_corrections


def _build_r2_user_followup(
    tokens: list[dict],
    goal: str,
    candidate_misrecognitions: list[dict],
) -> str:
    """R2 仅追加 user：主旨/粗分块/候选列表以 R1 assistant JSON 为准，此处只给任务与句面锚点。"""
    # 与 R1 相同的首行目标语，便于模型在第二轮仍对齐用户意图
    goal_line = f"用户目标：{goal}" if goal else "用户目标：无特定目标，提取核心内容"

    # R2 仍展示**原始** tokens 行（非 cleaned），供核对 old/nth 锚点；含空 text 句以防索引错位
    lines = [f"[{t['index']}] {t['text']}" for t in tokens]
    text_block = "\n".join(lines)

    # index → 原句文本，用于展开 candidate_misrecognitions 对照段
    token_map = {t["index"]: t["text"] for t in tokens}

    if candidate_misrecognitions:
        # 逐条展开「错词 + 候选」与当前句文本，降低模型抄错句的风险
        cand_lines = []
        for c in candidate_misrecognitions:
            ann_idx = c["annotation_index"]  # 句级 index，与 tokens 对齐
            original = token_map.get(ann_idx, "（未找到原句）")  # 防御：R1 若给错 index
            cand_lines.append(
                f"  - [{ann_idx}] 原句：\"{original}\"\n"
                f"    错词：\"{c['wrong']}\" → 候选：{c['suggestions']}"
            )
        # 多行拼成「核对」小节，插入 prompt 下半部
        cand_section = (
            "上一轮 JSON 中 candidate_misrecognitions 与下列原句核对（若不一致以原句为准）：\n"
            + "\n".join(cand_lines)
        )
    else:
        # 无候选时明确告知，避免模型虚构上一轮内容
        cand_section = "上一轮未输出 candidate_misrecognitions 或为空。"

    # R2 user 正文：提醒「上一轮 JSON 在 assistant」+ 全量句面 + 核对段 + 三项任务；JSON 示例仍由 intelligence_llm 追加
    return f"""{goal_line}

【阶段定位】当前阶段：2a 理解层 · 第 2 轮（R2）。本轮任务：在 R1 结果上精化主旨（purpose）、精化分块（outline_blocks）、输出可执行的稀疏纠错表（corrections）；程序将根据 corrections 在原文上替换生成消歧句面，你不得在 JSON 中改写整句原文，只能输出 index/old/nth/new 结构化纠错项。

你上一轮已在 assistant 消息中输出 JSON（含 purpose_rough、outline_blocks_rough、candidate_misrecognitions）。请在保持与上一轮一致的前提下，完成本回合输出（字段名与语义见下方 JSON 格式说明）。

以下是完整标注序列（格式：[index] 文字内容），用于核对纠错锚点：

{text_block}

{cand_section}

请完成以下任务：

1. 精化核心论点与叙事意图（purpose），比粗糙版更准确、完整
   - 聚焦“说话者要论证/传达什么”，以及贯穿全文的核心结论
   - 叙事结构细节（先讲什么/再讲什么）由 outline_blocks 承载，purpose 无需重复

2. 将内容按主题/段落划分为若干块（outline_blocks）：**每个元素**必须包含整数 `start_index`、整数 `end_index`、字符串 `summary`（与下方 JSON 示例键名一致）。**禁止**用其它字段名表示 index 区间。

3. 对上一轮 candidate_misrecognitions 中确认存在误识的条目，给出唯一纠错规则（corrections）
   每条纠错包含：
   - index：发生误识的句子 index
   - old：原文中的错误子串（必须是该句子的真实子串）
   - nth：该子串在原句中从左到右第几次出现（1-based，第一次出现填 1）
   - new：替换为的正确内容（必须来自上一轮候选，不得自造新词）

   规则：
   - 若无法确定唯一正确词，宁可不输出该条
   - old 和 nth 必须能在该句子中精确定位
   - 同一句子内多条纠错，在原文中不得存在字符重叠

请以 JSON 格式输出。"""


def _get_r2_schema() -> dict:
    """R2 的 JSON Schema：与 `_build_r2_user_followup` 三项任务对齐；多轮第二跳追加校验。"""
    return {
        "type": "object",  # 根对象；与 R1 相同 json_object 契约
        "additionalProperties": False,
        "properties": {
            "purpose": {  # 任务 1：精化主旨
                "type": "string",
                "description": "精化后的主旨描述",
            },
            "outline_blocks": {  # 任务 2：精化分块（与 R1 粗块独立，可重划边界）
                "type": "array",
                "description": "内容分块列表",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "start_index": {"type": "integer"},
                        "end_index": {"type": "integer"},
                        "summary": {"type": "string", "description": "块内容摘要"},
                    },
                    "required": ["start_index", "end_index", "summary"],
                },
            },
            "corrections": {  # 任务 3：可执行纠错表（程序 `_apply_corrections` 消费）
                "type": "array",
                "description": "纠错规则列表（仅包含确认的误识，不确定时宁可不输出）",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "index": {
                            "type": "integer",
                            "description": "发生误识的句子 index",
                        },
                        "old": {
                            "type": "string",
                            "description": "原文中的错误子串",
                        },
                        "nth": {
                            "type": "integer",
                            "description": "该子串在原句中第几次出现（1-based）",
                        },
                        "new": {
                            "type": "string",
                            "description": "替换为的正确内容（来自 R1 候选）",
                        },
                    },
                    "required": ["index", "old", "nth", "new"],
                },
            },
        },
        "required": ["purpose", "outline_blocks", "corrections"],  # 三块全必填；corrections 可为 []
    }


# ============================================================================
# 程序步骤：纠错算法
# ============================================================================

def _apply_corrections_to_sentence(
    sentence: str,
    corrections: list[Correction],
) -> str:
    """
    在原文上一次性应用所有纠错规则，避免逐条替换导致的位置漂移与幽灵匹配。

    算法分两阶段：
      1. 锚定 — 在不可变的原文上，为每条规则定位字符区间 (start, end)
      2. 拼接 — 按区间从左到右，用「原文片段 + 替换词」交替拼出结果

    Args:
        sentence:    原始 ASR 转录文本
        corrections: 纠错规则列表，每条包含 nth（1-based）/ old / new

    Returns:
        纠正后的完整字符串

    Raises:
        ValueError: 找不到第 nth 个匹配，或多条规则的区间存在重叠
    """
    # ── 阶段 1：在原文上锚定每条规则的字符区间 ──

    anchored: list[tuple[int, int, str]] = []  # [(start, end, replacement), ...]

    for c in corrections:
        start = 0
        idx = -1
        for i in range(c.nth):
            idx = sentence.find(c.old, start)
            if idx == -1:
                raise ValueError(
                    f"原文中找不到第 {c.nth} 个 '{c.old}'（仅找到 {i} 个）"
                )
            if i < c.nth - 1:
                start = idx + 1
        anchored.append((idx, idx + len(c.old), c.new))

    # ── 冲突检测：按 start 排序后检查区间是否重叠 ──

    anchored.sort(key=lambda t: t[0])

    for i in range(1, len(anchored)):
        prev_start, prev_end, prev_new = anchored[i - 1]
        curr_start, curr_end, curr_new = anchored[i]
        if curr_start < prev_end:
            raise ValueError(
                f"纠错区间重叠: "
                f"({prev_start}:{prev_end})→'{prev_new}' 与 "
                f"({curr_start}:{curr_end})→'{curr_new}'"
            )

    # ── 阶段 2：一次性拼接 ──
    #
    #   原文:  ───[保留]───[替换1]───[保留]───[替换2]───[保留]───
    #   cursor 从 0 开始，每次先拼 cursor→start 的原文片段，再拼替换词

    parts: list[str] = []
    cursor = 0

    for start, end, replacement in anchored:
        parts.append(sentence[cursor:start])
        parts.append(replacement)
        cursor = end

    parts.append(sentence[cursor:])

    return "".join(parts)


def _build_sparse_cleaned_annotations(
    token_text_view: dict[int, str],
    raw_corrections: list[dict],
) -> list[dict]:
    """按 index 分组 corrections，逐句应用，生成稀疏 cleaned_annotations。

    Args:
        token_text_view: 只读的 index -> 句面原文（来自 ``tokens[].text``）
        raw_corrections: R2 输出的纠错列表
                         [{"index": int, "old": str, "nth": int, "new": str}, ...]

    Returns:
        稀疏 cleaned_annotations [{"annotation_index": int, "cleaned_content": str}, ...]
        仅包含内容发生变化的条目。
    """
    # 按 annotation index 分组
    grouped: defaultdict[int, list[Correction]] = defaultdict(list)
    for raw in raw_corrections:
        grouped[raw["index"]].append(
            Correction(nth=raw["nth"], old=raw["old"], new=raw["new"])
        )

    cleaned: list[dict] = []
    for ann_index, corrs in grouped.items():
        original = token_text_view.get(ann_index)
        if original is None:
            logger.warning(
                "[2a] corrections 中 index=%s 不存在于 tokens，跳过", ann_index
            )
            continue
        try:
            result = _apply_corrections_to_sentence(original, corrs)
        except ValueError as e:
            logger.warning(
                "[2a] index=%s 纠错失败，跳过该句 (%s)", ann_index, e
            )
            continue

        if result != original:
            cleaned.append({"annotation_index": ann_index, "cleaned_content": result})

    return cleaned


def _densify_cleaned_annotations_from_tokens(
    tokens: list[dict],
    sparse_cleaned_annotations: list[dict],
) -> list[dict]:
    """将稀疏 cleaned_annotations 回填为稠密全量序列（与 ``tokens`` 等长）。

    规则：
    - 若某 index 在 sparse_cleaned_annotations 中，使用对应 cleaned_content。
    - 否则回填 ``tokens[i].text``。
    - ``annotation_index`` 字段名沿用 MVP 契约，与 ``tokens[].index`` 对齐。
    """
    sparse_map = {
        int(item["annotation_index"]): item["cleaned_content"]
        for item in sparse_cleaned_annotations
    }

    dense: list[dict] = []
    for i, tok in enumerate(tokens):
        ann_index = int(tok["index"])
        if ann_index != i:
            raise ValueError(
                f"tokens index 不连续，无法构造稠密 cleaned_annotations: "
                f"位置{i} 实际 index={ann_index}"
            )
        dense.append({
            "annotation_index": ann_index,
            "cleaned_content": sparse_map.get(ann_index, str(tok.get("text", ""))),
        })

    return dense


# ============================================================================
# 回流支持：run_2a_comprehension_reflow
# ============================================================================

def _build_reflow_purpose_prompt(
    tokens: list[dict],
    goal: str,
    current_purpose: str,
    current_outline_blocks: list[dict],
    feedback_text: str,
) -> str:
    """构造 purpose_drift 回流的 LLM prompt。

    与 R2 类似的单轮精化调用，但注入了用户反馈文本作为主旨修正指导。
    不要求输出 corrections（corrections 可为空数组）。

    Args:
        tokens: 句面列表（index + text）
        goal: 用户剪辑目标
        current_purpose: 当前 comprehension.purpose
        current_outline_blocks: 当前 comprehension.outline_blocks
        feedback_text: 用户提交的主旨修正反馈
    """
    goal_line = f"用户目标：{goal}" if goal else "用户目标：无特定目标，提取核心内容"

    lines = [f"[{t['index']}] {t['text']}" for t in tokens if t.get("text", "").strip()]
    text_block = "\n".join(lines)

    # 序列化当前分块供模型参考
    blocks_desc = ""
    if current_outline_blocks:
        block_lines = []
        for b in current_outline_blocks:
            block_lines.append(
                f"  - [{b.get('start_index', '?')}–{b.get('end_index', '?')}] "
                f"{b.get('summary', b.get('topic', ''))}"
            )
        blocks_desc = "当前分块：\n" + "\n".join(block_lines)
    else:
        blocks_desc = "当前分块：（无）"

    return f"""{goal_line}

【阶段定位】当前阶段：2a 理解层 · 主旨回流精化。用户对当前主旨提出了修正意见，请根据用户反馈重新精化主旨（purpose）和内容分块（outline_blocks）。本轮不需要输出纠错（corrections 输出空数组即可）。

当前主旨：{current_purpose}

{blocks_desc}

【用户反馈】
{feedback_text}

以下是视频 ASR 转写文本（格式：[index] 文字内容）：

{text_block}

请完成以下任务：

1. 根据用户反馈，精化核心论点与叙事意图（purpose）
   - 充分考虑用户的修正意见
   - 聚焦"说话者要论证/传达什么"，以及贯穿全文的核心结论

2. 根据更新后的主旨，重新划分内容分块（outline_blocks）：**每个元素**必须包含整数 `start_index`、整数 `end_index`、字符串 `summary`。

3. corrections 输出空数组（本轮不做纠错）。

请以 JSON 格式输出。"""


def run_2a_comprehension_reflow(
    manifest_dict: dict,
    *,
    reflow_mode: str,  # "purpose_drift" or "keyword_correction"
    feedback_text: str = "",
    correction: dict | None = None,
    on_phase_save: Callable[[str, dict], None] | None = None,
    on_chunk: Callable | None = None,
) -> dict:
    """2a 回流入口：根据 reflow_mode 更新 comprehension 的部分字段。

    两种模式：

    purpose_drift（F1）：
        - 不重跑 R1，不重建 cleaned_annotations
        - 单轮 LLM 调用（call_structured / stage=r2），将用户反馈注入 prompt
        - 仅更新 comprehension.purpose 和 comprehension.outline_blocks

    keyword_correction（F2）：
        - 不调用 LLM
        - 将用户提供的 {index, old, new} 转为 Correction(nth=1)
        - 在现有 cleaned_annotations 上直接替换（找到 annotation_index 匹配的条目，
          将 old 替换为 new），避免从头重建整条 corrections 链
        - 更新 comprehension.cleaned_annotations

    Args:
        manifest_dict: 包含 tokens、goal、comprehension 的工作数据
        reflow_mode: "purpose_drift" 或 "keyword_correction"
        feedback_text: purpose_drift 模式下的用户反馈文本
        correction: keyword_correction 模式下的纠错字典 {"index": int, "old": str, "new": str}
        on_phase_save: 可选回调，回流完成后调用

    Returns:
        更新了 comprehension 部分字段的 manifest_dict
    """
    logger.info("[2a-reflow] 模式: %s", reflow_mode)

    comprehension = manifest_dict["comprehension"]
    tokens = manifest_dict["tokens"]
    goal = manifest_dict.get("goal", "")

    if reflow_mode == "purpose_drift":
        # ── F1: 单轮 LLM 精化 purpose + outline_blocks ──
        prompt = _build_reflow_purpose_prompt(
            tokens,
            goal,
            current_purpose=comprehension["purpose"],
            current_outline_blocks=comprehension.get("outline_blocks", []),
            feedback_text=feedback_text,
        )
        schema = _get_r2_schema()

        response = call_structured(
            build_messages(prompt, schema), schema, "r2", on_chunk=on_chunk,
        ).data

        # 仅更新 purpose 和 outline_blocks；cleaned_annotations 不变
        comprehension["purpose"] = response["purpose"]
        comprehension["outline_blocks"] = response.get("outline_blocks", [])

        logger.info("[2a-reflow] purpose_drift 完成 - 新主旨: %s...", comprehension["purpose"][:60])
        logger.info("[2a-reflow] 新分块: %d 块", len(comprehension["outline_blocks"]))

        if on_phase_save is not None:
            on_phase_save(
                "2a_reflow_purpose",
                {"comprehension": copy.deepcopy(comprehension)},
            )

    elif reflow_mode == "keyword_correction":
        # ── F2: 程序步骤，无 LLM ──
        if correction is None:
            raise ValueError("keyword_correction 模式需要提供 correction 参数")

        corr_index = correction["index"]
        corr_old = correction["old"]
        corr_new = correction["new"]

        cleaned_annotations = comprehension["cleaned_annotations"]

        # 在现有 cleaned_annotations 中找到 annotation_index 匹配的条目，
        # 直接在 cleaned_content 上替换 old → new
        found = False
        for entry in cleaned_annotations:
            if int(entry["annotation_index"]) == corr_index:
                if corr_old in entry["cleaned_content"]:
                    entry["cleaned_content"] = entry["cleaned_content"].replace(
                        corr_old, corr_new, 1
                    )
                    found = True
                    logger.info(
                        "[2a-reflow] keyword_correction: index=%d, '%s' → '%s'",
                        corr_index, corr_old, corr_new,
                    )
                else:
                    logger.warning(
                        "[2a-reflow] keyword_correction: index=%d 的 cleaned_content 中"
                        "找不到子串 '%s'",
                        corr_index, corr_old,
                    )
                break

        if not found:
            logger.warning(
                "[2a-reflow] keyword_correction: 未找到 annotation_index=%d 或子串不匹配",
                corr_index,
            )

        logger.info("[2a-reflow] keyword_correction 完成")

        if on_phase_save is not None:
            on_phase_save(
                "2a_reflow_keyword",
                {"comprehension": copy.deepcopy(comprehension)},
            )

    else:
        raise ValueError(f"未知 reflow_mode: {reflow_mode!r}，合法值: 'purpose_drift', 'keyword_correction'")

    return manifest_dict
