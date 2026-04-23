"""Layer 2 / 2b 决策子阶段

## 职责
基于 2a 的理解结果，对每个 speech 标注做出保留/删除决策，生成 keep_mask。
这是智能层的核心输出，直接决定最终视频的内容。

## 决策逻辑
- 输入：2a 产出的稠密消歧文本 + 主旨 + 分块信息 + 用户目标
- 任务：口语转录精细清洗（重复起句、语气词、离题插入等）并结合用户目标
- 输出：对每个 annotation index 输出 keep=true 或 keep=false

## 输入 Schema
manifest_dict = {
    "tokens": [                 # JSON2 句面（仅 index + text；无时间轴）
        {
            "index": int,
            "text": str,
        }
    ],
    "comprehension": {          # 来自 2a
        "purpose": str,             # 精化主旨
        "cleaned_annotations": [    # 稠密消歧文本（与 tokens 等长对齐）
            {
                "annotation_index": int,
                "cleaned_content": str
            }
        ],
        "outline_blocks": [         # 内容分块
            {
                "start_index": int,
                "end_index": int,
                "summary": str
            }
        ]
    },
    "goal": str                 # 用户目标
}

## 输出 Schema
manifest_dict["keep_mask"] = [
    {
        "index": int,           # 对应 tokens[].index
        "keep": bool           # True=保留, False=删除（MVP 仅布尔，不用 null）
    },
    ...
]

## 注意
- keep_mask 长度必须等于 tokens 长度
- keep_mask 与 tokens 通过 index 一一对应
- 每条 keep 均为 bool，与 JSON3 / intelligence-layer2-mvp 一致

## 2b 模式（对照实验）
- ``mode="single"``：单次 LLM，传入全文句列。
- ``mode="chunked"``：按 ``outline_blocks`` 将句级 index 分区；每 outline 块内若句数
  超过 ``config.toml`` 中 ``[intelligence].two_b_block_size_limit``（默认 50），
  再按该上限二次拆分为子块，每子块一次 LLM；子块 prompt 仍含全文叙事弧。
  若 ``outline_blocks`` 为空则自动回退为 ``single``。
"""

from __future__ import annotations

import logging
from typing import Literal

from autosmartcut.config import load_config
from autosmartcut.intelligence_llm import call_llm_structured

TwoBMode = Literal["single", "chunked"]

logger = logging.getLogger(__name__)


# ============================================================================
# 模型参数（便于调试）
# ============================================================================

# 2b：开启思考链，便于按检查清单逐句比对后再输出 decisions
ENABLE_REASONING = True

# 温度：偏低，决策需要确定性和稳定性（非 reasoner 路径下生效）
TEMPERATURE = 0.2


def _get_two_b_block_size_limit() -> int:
    """从 config.toml [intelligence] 读取单 outline 块内二次拆分上限句数。"""
    return max(1, load_config(None).intelligence.two_b_block_size_limit)


def _two_b_shared_task_and_output_instructions() -> str:
    """2b single / chunked 共用的任务说明、检查清单与输出约定（不含阶段定位与句列）。"""
    return r"""【删除规则·逐句核查清单】
对「待决策句面」中每一句依次检查；**任一条件命中则 keep=false**（条件不互斥时以最有利于成片连贯性的解释为准）。

1. **纯语气词/填充词**：整句仅为「啊」「嗯」「哎」「呃」「对」「嗯嗯」等，无实质信息。
   → 删：整句只有一个「啊」且无其它语素。
   → 留：「不是完全不行」——虽有口语感，但承载转折语义，不是纯语气词。

2. **重复起句 / 口吃重启**：本句与**前后最多 5 句**内某句语义相同或为其更短、更不完整的版本；只保留**最完整**的一句，较短的不完整重启句删。
   → 删：「我讲一下为什么」而后文有更完整的「我现在讲一下为什么」时，删较短句。
   → 删：「好好读书」而后文有更完整的「好好读书行不行」时，删较短句。
   → 留：在同窗口内已是最完整表述的那一句。

3. **连续重复**：相邻句内容几乎完全相同，只保留一句（通常保留较早出现的一句）。

4. **离题插入**：回应弹幕、感谢打赏/礼物、与当前论述主线明显无关的穿插。

5. **冗余举例**：同一论点已用 1–2 个例子充分说明后，后续堆叠的同类举例可删减；保留最有力的 1–2 个即可。

【保留原则】
- **不确定时 keep=true**：误保留比误删更易在后续人工阶段修正。
- 口语化、句中夹杂语气词但整句仍承载新信息或推进论述，**一般保留**。

【推理要求】
在输出 JSON 之前完成思考：对「待决策句面」中**每一句**依次执行——读出本句 → 向前最多浏览 5 句、向后最多浏览 5 句并比对 → 对照上述规则 1–5 → 再定 keep。**须逐句完成**，禁止仅凭整体印象批量下结论。思考过程不要写入 JSON 外的正文（最终只输出 JSON）。

【输出要求】
- `decisions` 为数组；每项含 `index`（全文级整数，须与上方列表一致）与 `keep`（布尔）。
- **必须覆盖**上方「待决策句面」列出的**全部** index，不得遗漏；不得对未列出的 index 输出决策。
- 句面文字仅出现在用户给出的列表中；你不得在 JSON 中改写、润色或重述原文句子。

请以 JSON 格式输出。"""


# ============================================================================
# 主入口
# ============================================================================

def run_2b_decision(
    manifest_dict: dict,
    *,
    mode: TwoBMode = "single",
    review_fixes: list[dict] | None = None,
) -> dict:
    """2b 决策子阶段：输出 keep_mask

    Args:
        manifest_dict: 包含 tokens、comprehension、goal 的工作数据
        mode: ``single`` 单次全文调用；``chunked`` 按 2a 分块多次调用后合并。
        review_fixes: 2c 审核返回的修正指令列表（修正重跑时由编排层传入）。
            每项含 ``requirement``、``missing_indices``、``note``。
            为 None 或空列表时表示首次调用，不注入审核反馈。

    Returns:
        追加了 keep_mask 字段的 manifest_dict
    """
    is_fix_rerun = bool(review_fixes)
    logger.info(
        "[2b] 决策子阶段开始%s",
        "（2c 审核修正重跑）" if is_fix_rerun else "",
    )

    # 句面 JSON2：仅 index + text；决策语义以 comprehension 稠密文本为准
    tokens = manifest_dict["tokens"]
    # 2a 产物：purpose / cleaned_annotations / outline_blocks 等
    comprehension = manifest_dict.get("comprehension", {})
    # 用户剪辑意图，会写入 prompt 首行「用户目标」
    goal = manifest_dict.get("goal", "")
    # F3 回流时编排器注入的用户选择意见（临时字段）
    selection_opinion = str(manifest_dict.get("_selection_opinion", ""))

    if mode not in ("single", "chunked"):
        raise ValueError(f"mode 须为 'single' 或 'chunked'，实际: {mode!r}")
    if mode == "chunked":
        keep_mask = _generate_keep_mask_chunked(
            tokens, comprehension, goal, review_fixes=review_fixes,
            selection_opinion=selection_opinion,
        )
    else:
        keep_mask = _generate_keep_mask(
            tokens, comprehension, goal, review_fixes=review_fixes,
            selection_opinion=selection_opinion,
        )

    # 验证 keep_mask 格式
    if len(keep_mask) != len(tokens):
        raise ValueError(
            f"keep_mask 长度不匹配: {len(keep_mask)} != {len(tokens)}"
        )

    # 验证 index 对齐
    for i, entry in enumerate(keep_mask):
        if entry["index"] != tokens[i]["index"]:
            raise ValueError(
                f"keep_mask[{i}] 的 index 不对齐: "
                f"期望 {tokens[i]['index']}, 实际 {entry['index']}"
            )

    manifest_dict["keep_mask"] = keep_mask

    keep_count = sum(1 for e in keep_mask if e["keep"] is True)
    cut_count = sum(1 for e in keep_mask if e["keep"] is False)

    logger.info("[2b] 决策完成")
    logger.info("[2b] 保留: %d | 删除: %d", keep_count, cut_count)

    return manifest_dict


# ============================================================================
# LLM 决策生成
# ============================================================================

def _generate_keep_mask(
    tokens: list[dict],
    comprehension: dict,
    goal: str,
    *,
    review_fixes: list[dict] | None = None,
    selection_opinion: str = "",
) -> list[dict]:
    """调用 LLM 生成 keep_mask"""
    logger.info("[2b] 调用 LLM 生成决策")

    prompt = _build_prompt(tokens, comprehension, goal, review_fixes=review_fixes, selection_opinion=selection_opinion)
    schema = _get_schema()

    response = call_llm_structured(
        prompt=prompt,
        schema=schema,
        temperature=TEMPERATURE,
        enable_reasoning=ENABLE_REASONING,
    )

    llm_decisions = response.get("decisions", [])
    keep_mask = _build_keep_mask_from_llm_decisions(tokens, llm_decisions)

    return keep_mask


def _block_summary(block: dict) -> str:
    """兼容 2a 的 summary / topic 字段。"""
    s = block.get("summary")
    if isinstance(s, str) and s.strip():
        return s.strip()
    t = block.get("topic")
    if isinstance(t, str) and t.strip():
        return t.strip()
    return ""


def _partition_token_indices_by_blocks(
    tokens: list[dict],
    outline_blocks: list[dict],
) -> list[tuple[dict, list[int]]]:
    """按 2a 分块区间划分「列表下标」（顺序与 tokens 一致）。

    规则：按 ``outline_blocks`` 顺序扫描；每块认领满足
    ``start_index <= token.index <= end_index`` 且尚未被认领的句子。
    仍未认领的下标归入合成补块（全文级 index 不变）。

    Returns:
        (block_meta, positions) 列表；positions 为 ``tokens`` 的下标列表。
    """
    n = len(tokens)
    assigned = [False] * n
    out: list[tuple[dict, list[int]]] = []

    for b in outline_blocks:
        try:
            s = int(b["start_index"])
            e = int(b["end_index"])
        except (KeyError, TypeError) as ex:
            raise ValueError(f"outline_blocks 项缺少合法 start_index/end_index: {b!r}") from ex
        if e < s:
            raise ValueError(
                f"outline_blocks 无效区间: start_index={s} > end_index={e}"
            )
        positions: list[int] = []
        for i, tok in enumerate(tokens):
            if assigned[i]:
                continue
            idx = int(tok["index"])
            if s <= idx <= e:
                positions.append(i)
                assigned[i] = True
        out.append((b, positions))

    remainder = [i for i in range(n) if not assigned[i]]
    if remainder:
        lo = int(tokens[remainder[0]]["index"])
        hi = int(tokens[remainder[-1]]["index"])
        gap_meta = {
            "start_index": lo,
            "end_index": hi,
            "summary": "（未落在上述任一分块区间内的句子，按全文索引补齐）",
            "_synthetic_gap": True,
        }
        out.append((gap_meta, remainder))

    return out


def _narrative_arc_section(
    work: list[tuple[dict, list[int]]],
    tokens: list[dict],
) -> str:
    """由各块总结拼成叙事弧说明（1..N 编号）。"""
    lines: list[str] = []
    for i, (block_meta, positions) in enumerate(work, start=1):
        if not positions:
            continue
        lo = int(tokens[positions[0]]["index"])
        hi = int(tokens[positions[-1]]["index"])
        summ = _block_summary(block_meta)
        label = f"第{i}块 [全文 index {lo}–{hi}]"
        lines.append(f"{i}. {label}：{summ if summ else '（无摘要）'}")
    return "叙事弧（各块主旨，理解整体结构）：\n" + "\n".join(lines)


def _generate_keep_mask_chunked(
    tokens: list[dict],
    comprehension: dict,
    goal: str,
    *,
    review_fixes: list[dict] | None = None,
    selection_opinion: str = "",
) -> list[dict]:
    """按分块多次调用 LLM，合并为完整 keep_mask。"""
    outline_blocks = comprehension.get("outline_blocks", [])
    cleaned_annotations = comprehension.get("cleaned_annotations", [])
    _validate_dense_cleaned_vs_tokens(tokens, cleaned_annotations)

    if not outline_blocks:
        logger.info("[2b] chunked：outline_blocks 为空，回退为 single 单次调用")
        return _generate_keep_mask(
            tokens, comprehension, goal, review_fixes=review_fixes,
            selection_opinion=selection_opinion,
        )

    partitions = _partition_token_indices_by_blocks(tokens, outline_blocks)
    work = [(b, pos) for b, pos in partitions if pos]

    if not work:
        logger.warning("[2b] 分块后无任何句子，全部默认保留")
        return [{"index": int(tok["index"]), "keep": True} for tok in tokens]

    n_blocks = len(work)
    arc = _narrative_arc_section(work, tokens)
    purpose = comprehension.get("purpose", "")
    schema = _get_schema()
    merged: dict[int, bool] = {}

    block_limit = _get_two_b_block_size_limit()

    for ord1, (block_meta, positions) in enumerate(work, start=1):
        n_pos = len(positions)
        if n_pos > block_limit:
            sub_chunks = [
                positions[i : i + block_limit]
                for i in range(0, n_pos, block_limit)
            ]
            logger.info(
                "[2b] outline 块 %d/%d 共 %d 句，按 limit=%d 拆为 %d 个子块",
                ord1,
                n_blocks,
                n_pos,
                block_limit,
                len(sub_chunks),
            )
        else:
            sub_chunks = [positions]

        n_subs = len(sub_chunks)
        for sub_ord, sub_positions in enumerate(sub_chunks, start=1):
            logger.info(
                "[2b] chunked LLM outline 块 %d/%d · 子块 %d/%d，本批 %d 句",
                ord1,
                n_blocks,
                sub_ord,
                n_subs,
                len(sub_positions),
            )
            # 过滤出与本子块相关的修正指令
            chunk_fixes = _filter_fixes_for_chunk(
                review_fixes, {int(tokens[i]["index"]) for i in sub_positions}
            ) if review_fixes else None
            prompt = _build_prompt_chunked(
                purpose=purpose,
                goal=goal,
                block_ordinal=ord1,
                n_blocks=n_blocks,
                sub_ordinal=sub_ord,
                n_subs=n_subs,
                narrative_arc_section=arc,
                tokens=tokens,
                cleaned_annotations=cleaned_annotations,
                block_positions=sub_positions,
                block_meta=block_meta,
                review_fixes=chunk_fixes,
                selection_opinion=selection_opinion,
            )
            response = call_llm_structured(
                prompt=prompt,
                schema=schema,
                temperature=TEMPERATURE,
                enable_reasoning=ENABLE_REASONING,
            )
            llm_decisions = response.get("decisions", [])
            allowed = {int(tokens[i]["index"]) for i in sub_positions}
            chunk_map = _merge_chunk_decisions(llm_decisions, allowed)
            for idx, keep in chunk_map.items():
                if idx in merged:
                    logger.warning(
                        "[2b] index %s 在多块中重复决策，以后块为准", idx
                    )
                merged[idx] = keep

    synthetic = [
        {"index": int(tok["index"]), "keep": merged.get(int(tok["index"]), True)}
        for tok in tokens
    ]
    return _build_keep_mask_from_llm_decisions(tokens, synthetic)


def _filter_fixes_for_chunk(
    review_fixes: list[dict] | None,
    allowed_indices: set[int],
) -> list[dict] | None:
    """过滤出与本子块相关的修正项（missing_indices 与本块有交集的项）。

    Returns:
        过滤后的修正列表；若无相关项则返回 None。
    """
    if not review_fixes:
        return None
    result: list[dict] = []
    for fix in review_fixes:
        relevant = [i for i in fix.get("missing_indices", []) if i in allowed_indices]
        if relevant:
            result.append({
                "requirement": fix["requirement"],
                "missing_indices": relevant,
                "note": fix.get("note", ""),
            })
    return result if result else None


def _build_review_fixes_section(review_fixes: list[dict] | None) -> str:
    """构造审核修正指令区段。无修正时返回空字符串。"""
    if not review_fixes:
        return ""
    lines = [
        "【审核修正指令（本次为 2c 审核后的修正重跑）】",
        "上一轮决策存在以下问题，本轮须优先修正。对于下列指出应保留的 index，",
        "除非该句是纯语气词或与前后句完全重复，否则必须改为 keep=true：",
        "",
    ]
    for i, fix in enumerate(review_fixes, start=1):
        indices_str = ", ".join(str(idx) for idx in fix["missing_indices"])
        lines.append(f"{i}. 未满足条件：「{fix['requirement']}」")
        lines.append(f"   应保留但被删除的句子：index {indices_str}")
        if fix.get("note"):
            lines.append(f"   说明：{fix['note']}")
        lines.append("")
    return "\n".join(lines)


def _merge_chunk_decisions(
    llm_decisions: list[dict],
    allowed_global_indices: set[int],
) -> dict[int, bool]:
    """只接受属于本批次的全文 index；缺省默认保留。"""
    out: dict[int, bool] = {}
    for d in llm_decisions:
        idx = d.get("index")
        if not isinstance(idx, int):
            continue
        if idx not in allowed_global_indices:
            logger.warning("[2b] LLM 返回了非本批 index %s，已忽略", idx)
            continue
        out[idx] = bool(d.get("keep"))
    for idx in allowed_global_indices:
        if idx not in out:
            logger.warning("[2b] 本批 LLM 未覆盖 index %s，默认保留", idx)
            out[idx] = True
    return out


def _build_prompt_chunked(
    *,
    purpose: str,
    goal: str,
    block_ordinal: int,
    n_blocks: int,
    sub_ordinal: int,
    n_subs: int,
    narrative_arc_section: str,
    tokens: list[dict],
    cleaned_annotations: list[dict],
    block_positions: list[int],
    block_meta: dict,
    review_fixes: list[dict] | None = None,
    selection_opinion: str = "",
) -> str:
    """分块模式下单次 LLM 的 prompt。"""
    goal_line = f"用户目标：{goal}" if goal else "用户目标：无特定目标，提取核心内容"
    lo = int(tokens[block_positions[0]]["index"])
    hi = int(tokens[block_positions[-1]]["index"])
    range_line = f"本子块在全文中的 index 范围：{lo}–{hi}（共 {len(block_positions)} 句）"

    speech_lines = []
    for i in block_positions:
        idx = int(tokens[i]["index"])
        text = cleaned_annotations[i]["cleaned_content"]
        speech_lines.append(f"[{idx}] {text}")

    block_summ = _block_summary(block_meta)
    extra = ""
    if block_meta.get("_synthetic_gap"):
        extra = "\n说明：本块为程序根据全文 index 补齐的分区，不在 2a 原始 outline_blocks 中。"

    sub_line = (
        f"当前 outline 块：第 {block_ordinal}/{n_blocks} 块；"
        f"本子块：第 {sub_ordinal}/{n_subs} 批（同一 outline 块内按句数上限切分）。\n"
    )

    stage = (
        "【阶段定位】当前阶段：2b 决策层（口语转录清洗 + 成片取舍）。"
        "上游 2a 已产出主旨、分块摘要与稠密消歧句面（cleaned）；"
        "下游执行层将仅依据 keep_mask 与清单时间轴裁切视频。\n"
    )

    fixes_section = _build_review_fixes_section(review_fixes)

    opinion_section = ""
    if selection_opinion:
        opinion_section = (
            "【用户内容选择意见（F3 反馈，本轮须优先遵从）】\n"
            f"用户要求：{selection_opinion}\n\n"
        )

    shared = _two_b_shared_task_and_output_instructions()

    return f"""{goal_line}

{stage}{sub_line}{fixes_section}{opinion_section}内容主旨：{purpose}

{range_line}
本 outline 块摘要：{block_summ if block_summ else "（无）"}{extra}

{narrative_arc_section}

【待决策句面】（仅下列 index 须输出 decisions；须与原文逐字一致）
{chr(10).join(speech_lines)}

{shared}"""


def _build_prompt(
    tokens: list[dict],
    comprehension: dict,
    goal: str,
    *,
    review_fixes: list[dict] | None = None,
    selection_opinion: str = "",
) -> str:
    """构造 2b 决策的 Prompt（single：全文一次）。"""
    purpose = comprehension.get("purpose", "")
    cleaned_annotations = comprehension.get("cleaned_annotations", [])
    outline_blocks = comprehension.get("outline_blocks", [])
    _validate_dense_cleaned_vs_tokens(tokens, cleaned_annotations)

    if outline_blocks:
        block_lines = [
            f"  块 {i+1} [index {b['start_index']}-{b['end_index']}]: {_block_summary(b)}"
            for i, b in enumerate(outline_blocks)
        ]
        blocks_section = "内容分块：\n" + "\n".join(block_lines)
    else:
        blocks_section = "内容分块：无"

    speech_lines = []
    for i, tok in enumerate(tokens):
        idx = int(tok["index"])
        text = cleaned_annotations[i]["cleaned_content"]
        speech_lines.append(f"[{idx}] {text}")

    speech_text = "\n".join(speech_lines)

    goal_line = f"用户目标：{goal}" if goal else "用户目标：无特定目标，提取核心内容"

    stage = (
        "【阶段定位】当前阶段：2b 决策层 · single 模式（全文一次调用）。"
        "上游 2a 已产出主旨、分块摘要与稠密消歧句面；"
        "下游执行层将仅依据 keep_mask 裁切视频。\n"
    )

    fixes_section = _build_review_fixes_section(review_fixes)

    opinion_section = ""
    if selection_opinion:
        opinion_section = (
            "【用户内容选择意见（F3 反馈，本轮须优先遵从）】\n"
            f"用户要求：{selection_opinion}\n\n"
        )

    shared = _two_b_shared_task_and_output_instructions()

    return f"""{goal_line}

{stage}{fixes_section}{opinion_section}内容主旨：{purpose}

{blocks_section}

【待决策句面】（全文所有句；须与原文逐字一致）
{speech_text}

{shared}"""


def _validate_dense_cleaned_vs_tokens(
    tokens: list[dict],
    cleaned_annotations: list[dict],
) -> None:
    """校验 cleaned_annotations 为与 tokens 严格对齐的稠密序列。"""
    if len(cleaned_annotations) != len(tokens):
        raise ValueError(
            "comprehension.cleaned_annotations 必须为稠密全量序列："
            f"长度不匹配 {len(cleaned_annotations)} != {len(tokens)}"
        )

    for i, tok in enumerate(tokens):
        expected_idx = int(tok["index"])
        item = cleaned_annotations[i]
        actual_idx = item.get("annotation_index")
        if not isinstance(actual_idx, int):
            raise ValueError(
                "comprehension.cleaned_annotations.annotation_index 必须为整数："
                f"位置{i} 实际={actual_idx!r}"
            )
        if actual_idx != expected_idx:
            raise ValueError(
                "comprehension.cleaned_annotations 与 tokens 未对齐："
                f"位置{i} 期望 annotation_index={expected_idx}，实际={actual_idx}"
            )
        if "cleaned_content" not in item:
            raise ValueError(
                "comprehension.cleaned_annotations 缺少 cleaned_content："
                f"位置{i} annotation_index={expected_idx}"
            )
        if not isinstance(item["cleaned_content"], str):
            raise ValueError(
                "comprehension.cleaned_annotations.cleaned_content 必须为字符串："
                f"位置{i} annotation_index={expected_idx}"
            )


def _get_schema() -> dict:
    """2b 决策的输出 JSON Schema：供 intelligence_llm 生成示例尾缀 + 解析后校验。"""
    return {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "description": "对每个 speech 标注的决策列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {
                            "type": "integer",
                            "description": "对应 tokens[].index",
                        },
                        "keep": {
                            "type": "boolean",
                            "description": "true=保留, false=删除",
                        },
                    },
                    "required": ["index", "keep"],
                },
            },
        },
        "required": ["decisions"],
    }


def _build_keep_mask_from_llm_decisions(
    tokens: list[dict],
    llm_decisions: list[dict]
) -> list[dict]:
    """将 LLM 决策扩展为完整 keep_mask"""
    decision_map = {d["index"]: d["keep"] for d in llm_decisions}

    keep_mask = []
    for tok in tokens:
        idx = int(tok["index"])
        if idx not in decision_map:
            logger.warning("[2b] LLM 未覆盖 index %s，默认保留", idx)
            keep = True
        else:
            keep = bool(decision_map[idx])
        keep_mask.append({"index": idx, "keep": keep})

    return keep_mask
