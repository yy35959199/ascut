"""Layer 2 / 2b 决策子阶段

两阶段：R1 逐句口语清洗（decision_r1）→ R2 全文内容取舍（decision_r2）。
分块由 ``outline_blocks`` 驱动；无分块时整段作为单块。R2 在保留句数低于阈值时
走 single，否则按块并行（与 R1 相同的分区）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

from autosmartcut.config import load_config
from autosmartcut.nodes.l2.intelligence_llm import (
    StructuredResult,
    build_messages,
    call_structured,
)
from autosmartcut.nodes.l2.llm_concurrent import AdaptiveThrottle

if TYPE_CHECKING:
    from autosmartcut.nodes.l2.intelligence_2b_dispatch import (
        BlockResult,
        BlockStreamCollector,
        BlockTask,
    )

TwoBMode = Literal["single", "block"]

logger = logging.getLogger(__name__)

# R1 reason 枚举与 R2 行首标签共用（唯一来源）
REASON_LABELS: dict[str, str] = {
    "ok": "✓",
    "filler": "✗:语气",
    "stutter": "✗:重启",
    "duplicate": "✗:重复",
    "incomplete": "✗:断句",
}
_R1_REASON_ENUM = list(REASON_LABELS.keys())


def _r1_task_text() -> str:
    return r"""【逐句清洗规则】
对「待决策句面」中每一句依次检查，命中以下任一条件则 keep=false：

1. **纯语气词/填充词**：整句仅为「啊」「嗯」「哎」「呃」「对」「嗯嗯」「是的」等，无实质语义。
   判断标准：去掉该句后，前后句的语义连接不受影响。
   注意：「不是完全不行」虽有口语感，但承载转折语义，keep=true。

2. **口吃重启**：本句与前后 5 句内某句表达同一意思，且本句是更短、更不完整的版本。
   只保留窗口内最完整的一句，其余 keep=false。

3. **连续重复**：相邻句内容几乎完全相同（字面重复或仅差语气词），保留较早的一句。

4. **未完成断句**：句子明显说到一半中断，后续有完整版本接续。

以上四条之外的句子一律 keep=true。不确定时 keep=true。

【输出要求】
- `decisions` 数组，每项含 `index`（整数）、`keep`（布尔）、`reason`（字符串：
  `"ok"` / `"filler"` / `"stutter"` / `"duplicate"` / `"incomplete"`）。
- 必须覆盖上方「待决策句面」的全部 index，不得遗漏。

请以 JSON 格式输出。"""


def _r2_task_text() -> str:
    return r"""【内容取舍规则】
每句已标注 R1 清洗结果（行首标签：[✓] 表保留倾向 / [✗:…] 表建议删除）。你须在此基础上做全文级内容取舍。

对 R1 标注为删除倾向的句子：
- 大部分直接确认 keep=false。
- 若删除后导致前后语义断裂，或该句实际承载了论述推进，改为 keep=true。

对 R1 标注为保留倾向的句子，检查以下条件（命中则 keep=false）：

1. **离题插入**：回应弹幕、感谢打赏/礼物、与当前论述主线无关的穿插。

2. **冗余举例**：同一论点已用 1–2 个例子充分说明后，后续堆叠的同类举例。保留最有力的 1–2 个。

3. **过渡性重复**：跨段落的总结性重述，若与前文论点完全重复且不引入新信息，可删。

以上条件之外的句子一律 keep=true。不确定时 keep=true。

【输出要求】
- `decisions` 数组，每项含 `index`（整数）与 `keep`（布尔）。
- 必须覆盖上方「待决策句面」的全部 index，不得遗漏。

请以 JSON 格式输出。"""


def run_2b_decision(
    manifest_dict: dict,
    *,
    mode: TwoBMode = "single",
    review_fixes: list[dict] | None = None,
    on_chunk: Callable | None = None,
    collector: "BlockStreamCollector | None" = None,
) -> dict:
    """2b 决策：R1 → R2，输出 keep_mask。

    ``mode`` 保留兼容，内部始终走 R1+R2 流水线。
    ``collector`` 为 None 时内部创建临时收集器；若提供 ``on_chunk``，会订阅收集器以兼容旧接口。
    """
    from autosmartcut.nodes.l2.intelligence_2b_dispatch import BlockStreamCollector

    _ = mode  # 保留参数

    is_fix_rerun = bool(review_fixes)
    logger.info(
        "[2b] 决策子阶段开始%s | tokens=%d",
        "（2c 审核修正重跑）" if is_fix_rerun else "",
        len(manifest_dict.get("tokens", [])),
    )

    tokens = manifest_dict["tokens"]
    comprehension = manifest_dict.get("comprehension", {})
    goal = manifest_dict.get("goal", "")
    selection_opinion = str(manifest_dict.get("_selection_opinion", ""))

    col = collector if collector is not None else BlockStreamCollector()
    if on_chunk:
        col.subscribe(lambda _ord, ch: on_chunk(ch))

    cleaned_annotations = comprehension.get("cleaned_annotations", [])
    _validate_dense_cleaned_vs_tokens(tokens, cleaned_annotations)

    outline_blocks = comprehension.get("outline_blocks", []) or []

    if outline_blocks:
        partitions = _partition_token_indices_by_blocks(tokens, outline_blocks)
        work = [(b, pos) for b, pos in partitions if pos]
    else:
        n = len(tokens)
        work = [
            (
                {
                    "start_index": int(tokens[0]["index"]),
                    "end_index": int(tokens[-1]["index"]),
                    "summary": "（全文单块）",
                    "_synthetic_gap": True,
                },
                list(range(n)),
            )
        ]

    if not work:
        logger.warning("[2b] 无可决策块，全部保留")
        km = [{"index": int(t["index"]), "keep": True} for t in tokens]
        manifest_dict["keep_mask"] = km
        return manifest_dict

    cfg_intel = load_config(None).intelligence
    throttle_initial = min(len(work), 8)

    from autosmartcut.nodes.l2.intelligence_2b_dispatch import (
        BlockResult,
        BlockTask,
        dispatch_blocks_parallel,
    )

    throttle_r1 = AdaptiveThrottle(throttle_initial)

    schema_r1 = _get_r1_schema()
    r1_tasks: list[BlockTask] = []
    n_blocks_r1 = len(work)
    for ord1, (block_meta, positions) in enumerate(work, start=1):
        lo = int(tokens[positions[0]]["index"])
        hi = int(tokens[positions[-1]]["index"])
        summary = (
            f"R1 口语清洗 | 第 {ord1}/{n_blocks_r1} 块 | index {lo}–{hi} | 共 {len(positions)} 句"
        )
        prompt = _build_prompt_r1_block(
            block_ordinal=ord1,
            n_blocks=n_blocks_r1,
            tokens=tokens,
            cleaned_annotations=cleaned_annotations,
            block_positions=positions,
        )
        allowed = {int(tokens[i]["index"]) for i in positions}
        r1_tasks.append(
            BlockTask(
                block_ordinal=ord1,
                n_blocks=n_blocks_r1,
                stage="decision_r1",
                messages=build_messages(prompt, schema_r1),
                schema=schema_r1,
                allowed_indices=allowed,
                input_summary=summary,
            )
        )
        col.register_block(ord1)

    def _sanitize_r1(task: BlockTask, sr: StructuredResult) -> BlockResult:
        decisions_raw = sr.data.get("decisions", [])
        sanitized: list[dict] = []
        for d in decisions_raw:
            idx = d.get("index")
            if not isinstance(idx, int) or idx not in task.allowed_indices:
                continue
            reason = str(d.get("reason", "ok"))
            if reason not in REASON_LABELS:
                reason = "ok"
            sanitized.append({
                "index": idx,
                "keep": bool(d.get("keep")),
                "reason": reason,
            })
        for idx in task.allowed_indices:
            if not any(x["index"] == idx for x in sanitized):
                sanitized.append({"index": idx, "keep": True, "reason": "ok"})
        return BlockResult(task.block_ordinal, sr, sanitized)

    r1_results = dispatch_blocks_parallel(
        r1_tasks, _sanitize_r1, throttle_r1, col,
    )
    preliminary = _merge_r1_block_results(tokens, r1_results)

    remaining_kept = sum(1 for d in preliminary if d.get("keep"))
    threshold = cfg_intel.two_b_r2_single_threshold

    arc_section = _narrative_arc_section(work, tokens)
    purpose = comprehension.get("purpose", "")
    block_limit = cfg_intel.two_b_block_size_limit

    schema_r2 = _get_schema()

    if remaining_kept < threshold:
        prompt_r2 = _build_prompt_r2_single(
            tokens=tokens,
            comprehension=comprehension,
            goal=goal,
            preliminary=preliminary,
            narrative_arc_section=arc_section,
            review_fixes=review_fixes,
            selection_opinion=selection_opinion,
        )
        logger.info(
            "[2b] R2 single（保留句 %d < 阈值 %d）",
            remaining_kept,
            threshold,
        )
        r2_single_ord = n_blocks_r1 + 1
        col.register_block(r2_single_ord)
        sr2 = call_structured(
            build_messages(prompt_r2, schema_r2),
            schema_r2,
            "decision_r2",
            on_chunk=col.make_on_chunk(r2_single_ord) if col else None,
        )
        llm_r2 = sr2.data.get("decisions", [])
        r2_map = _merge_chunk_decisions(llm_r2, {int(t["index"]) for t in tokens})
    else:
        throttle_r2 = AdaptiveThrottle(min(len(work), 8))
        r2_tasks: list[BlockTask] = []
        n_blocks_r2 = len(work)
        base_ord = n_blocks_r1 + 1
        for ord1, (block_meta, positions) in enumerate(work, start=1):
            lo = int(tokens[positions[0]]["index"])
            hi = int(tokens[positions[-1]]["index"])
            n_pos = len(positions)
            if block_limit > 0 and n_pos > block_limit:
                logger.warning(
                    "[2b] R2 outline 块 %d/%d 共 %d 句，超过 two_b_block_size_limit=%d",
                    ord1,
                    n_blocks_r2,
                    n_pos,
                    block_limit,
                )
            prompt_b = _build_prompt_r2_block(
                purpose=purpose,
                goal=goal,
                block_ordinal=ord1,
                n_blocks=n_blocks_r2,
                narrative_arc_section=arc_section,
                tokens=tokens,
                cleaned_annotations=cleaned_annotations,
                block_positions=positions,
                block_meta=block_meta,
                preliminary=preliminary,
                review_fixes=review_fixes,
                selection_opinion=selection_opinion,
            )
            allowed = {int(tokens[i]["index"]) for i in positions}
            bo = base_ord + ord1 - 1
            r2_tasks.append(
                BlockTask(
                    block_ordinal=bo,
                    n_blocks=n_blocks_r2,
                    stage="decision_r2",
                    messages=build_messages(prompt_b, schema_r2),
                    schema=schema_r2,
                    allowed_indices=allowed,
                    input_summary=(
                        f"R2 内容取舍 | 第 {ord1}/{n_blocks_r2} 块 | index {lo}–{hi}"
                    ),
                )
            )
            col.register_block(bo)

        def _sanitize_r2(task: BlockTask, sr: StructuredResult) -> BlockResult:
            decisions_raw = sr.data.get("decisions", [])
            sanitized = []
            for d in decisions_raw:
                idx = d.get("index")
                if not isinstance(idx, int) or idx not in task.allowed_indices:
                    continue
                sanitized.append({"index": idx, "keep": bool(d.get("keep"))})
            for idx in task.allowed_indices:
                if not any(x["index"] == idx for x in sanitized):
                    sanitized.append({"index": idx, "keep": True})
            return BlockResult(task.block_ordinal, sr, sanitized)

        r2_results = dispatch_blocks_parallel(
            r2_tasks, _sanitize_r2, throttle_r2, col,
        )
        merged_r2: dict[int, bool] = {}
        for br in sorted(r2_results, key=lambda x: x.block_ordinal):
            for d in br.decisions:
                merged_r2[int(d["index"])] = bool(d["keep"])
        r2_map = merged_r2

    keep_mask = _merge_preliminary_with_r2(tokens, preliminary, r2_map)

    if len(keep_mask) != len(tokens):
        raise ValueError(
            f"keep_mask 长度不匹配: {len(keep_mask)} != {len(tokens)}"
        )
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


def _merge_preliminary_with_r2(
    tokens: list[dict],
    preliminary: list[dict],
    r2_map: dict[int, bool],
) -> list[dict]:
    """R2 输出优先；缺省时回退 R1。"""
    out: list[dict] = []
    for i, tok in enumerate(tokens):
        idx = int(tok["index"])
        if idx in r2_map:
            out.append({"index": idx, "keep": r2_map[idx]})
        else:
            out.append({
                "index": idx,
                "keep": bool(preliminary[i].get("keep", True)),
            })
    return out


def _merge_r1_block_results(
    tokens: list[dict],
    results: list["BlockResult"],
) -> list[dict]:
    """合并各块 R1 决策为与 tokens 等长的稠密 preliminary（含 reason）。"""
    by_idx: dict[int, dict] = {}
    for br in sorted(results, key=lambda x: x.block_ordinal):
        for d in br.decisions:
            idx = d.get("index")
            if not isinstance(idx, int):
                continue
            reason = str(d.get("reason", "ok"))
            if reason not in REASON_LABELS:
                reason = "ok"
            by_idx[idx] = {
                "index": idx,
                "keep": bool(d.get("keep")),
                "reason": reason,
            }
    out: list[dict] = []
    for tok in tokens:
        idx = int(tok["index"])
        out.append(by_idx.get(idx, {"index": idx, "keep": True, "reason": "ok"}))
    return out


def _build_prompt_r1_block(
    *,
    block_ordinal: int,
    n_blocks: int,
    tokens: list[dict],
    cleaned_annotations: list[dict],
    block_positions: list[int],
) -> str:
    lo = int(tokens[block_positions[0]]["index"])
    hi = int(tokens[block_positions[-1]]["index"])
    speech_lines = []
    for i in block_positions:
        idx = int(tokens[i]["index"])
        text = cleaned_annotations[i]["cleaned_content"]
        speech_lines.append(f"[{idx}] {text}")
    rules = _r1_task_text()
    loc = (
        f"【阶段定位】2b · R1 口语清洗 | 第 {block_ordinal}/{n_blocks} 块 | "
        f"全文 index {lo}–{hi}（共 {len(block_positions)} 句）"
    )
    return f"""{rules}

【待决策句面】
{chr(10).join(speech_lines)}

{loc}"""


def _build_prompt_r2_single(
    *,
    tokens: list[dict],
    comprehension: dict,
    goal: str,
    preliminary: list[dict],
    narrative_arc_section: str,
    review_fixes: list[dict] | None,
    selection_opinion: str,
) -> str:
    purpose = comprehension.get("purpose", "")
    cleaned_annotations = comprehension.get("cleaned_annotations", [])
    goal_line = f"用户目标：{goal}" if goal else "用户目标：无特定目标，提取核心内容"
    fixes = _build_review_fixes_section(review_fixes)
    opinion_section = ""
    if selection_opinion:
        opinion_section = (
            "【用户内容选择意见（F3 反馈，本轮须优先遵从）】\n"
            f"用户要求：{selection_opinion}\n\n"
        )
    rules = _r2_task_text()
    lines = []
    for i, tok in enumerate(tokens):
        idx = int(tok["index"])
        text = cleaned_annotations[i]["cleaned_content"]
        pre = preliminary[i]
        tag = REASON_LABELS.get(pre.get("reason", "ok"), "✓")
        lines.append(f"[{tag}] [{idx}] {text}")
    speech = "\n".join(lines)
    stage = "【阶段定位】2b · R2 内容取舍（全文单次调用）\n"
    return f"""{rules}

{goal_line}

{stage}{fixes}{opinion_section}内容主旨：{purpose}

{narrative_arc_section}

【待决策句面】（R1 标注 + 原文）
{speech}"""


def _build_prompt_r2_block(
    *,
    purpose: str,
    goal: str,
    block_ordinal: int,
    n_blocks: int,
    narrative_arc_section: str,
    tokens: list[dict],
    cleaned_annotations: list[dict],
    block_positions: list[int],
    block_meta: dict,
    preliminary: list[dict],
    review_fixes: list[dict] | None,
    selection_opinion: str,
) -> str:
    goal_line = f"用户目标：{goal}" if goal else "用户目标：无特定目标，提取核心内容"
    lo = int(tokens[block_positions[0]]["index"])
    hi = int(tokens[block_positions[-1]]["index"])
    fixes = _build_review_fixes_section(review_fixes)
    opinion_section = ""
    if selection_opinion:
        opinion_section = (
            "【用户内容选择意见（F3 反馈，本轮须优先遵从）】\n"
            f"用户要求：{selection_opinion}\n\n"
        )
    rules = _r2_task_text()
    summ = _block_summary(block_meta)
    extra = ""
    if block_meta.get("_synthetic_gap"):
        extra = "\n说明：本块为程序补齐分区。"
    lines = []
    for i in block_positions:
        idx = int(tokens[i]["index"])
        text = cleaned_annotations[i]["cleaned_content"]
        pre = preliminary[i]
        tag = REASON_LABELS.get(pre.get("reason", "ok"), "✓")
        lines.append(f"[{tag}] [{idx}] {text}")
    speech = "\n".join(lines)
    loc = (
        f"【阶段定位】2b · R2 内容取舍 | 第 {block_ordinal}/{n_blocks} 块 | "
        f"index {lo}–{hi}{extra}"
    )
    return f"""{rules}

{goal_line}

{loc}

{fixes}{opinion_section}内容主旨：{purpose}

{narrative_arc_section}

本块摘要：{summ if summ else "（无）"}

【待决策句面】（仅下列 index；须输出全部）
{speech}"""


def _block_summary(block: dict) -> str:
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


def _build_review_fixes_section(review_fixes: list[dict] | None) -> str:
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


def _validate_dense_cleaned_vs_tokens(
    tokens: list[dict],
    cleaned_annotations: list[dict],
) -> None:
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


def _get_r1_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "index": {"type": "integer"},
                        "keep": {"type": "boolean"},
                        "reason": {
                            "type": "string",
                            "enum": _R1_REASON_ENUM,
                        },
                    },
                    "required": ["index", "keep", "reason"],
                },
            },
        },
        "required": ["decisions"],
    }


def _get_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decisions": {
                "type": "array",
                "description": "对每个 speech 标注的决策列表",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
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
    llm_decisions: list[dict],
) -> list[dict]:
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
