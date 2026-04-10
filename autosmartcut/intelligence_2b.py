"""Layer 2 / 2b 决策子阶段

## 职责
基于 2a 的理解结果，对每个 speech 标注做出保留/删除决策，生成 keep_mask。
这是智能层的核心输出，直接决定最终视频的内容。

## 决策逻辑
- 输入：2a 产出的稠密消歧文本 + 主旨 + 分块信息 + 用户目标
- 任务：判断每个句级标注是否与目标相关、是否值得保留
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
- ``mode="single"``：单次 LLM，传入全文句列（与 MVP 初版一致）。
- ``mode="chunked"``：按 ``outline_blocks`` 将句级 index 分区（全文级 index 不变），
  每块一次 LLM；每轮 prompt 含当前块号、叙事弧（各块总结）、当前块句列。
  若 ``outline_blocks`` 为空则自动回退为 ``single``。
"""

from __future__ import annotations

from typing import Literal

from autosmartcut.intelligence_llm import call_llm_structured

TwoBMode = Literal["single", "chunked"]


# ============================================================================
# 模型参数（便于调试）
# ============================================================================

# 2b 使用普通 chat 模型（文本分类任务，不需要 reasoner）
ENABLE_REASONING = False

# 温度：偏低，决策需要确定性和稳定性
TEMPERATURE = 0.2


# ============================================================================
# 主入口
# ============================================================================

def run_2b_decision(manifest_dict: dict, *, mode: TwoBMode = "single") -> dict:
    """2b 决策子阶段：输出 keep_mask

    Args:
        manifest_dict: 包含 tokens、comprehension、goal 的工作数据
        mode: ``single`` 单次全文调用；``chunked`` 按 2a 分块多次调用后合并。

    Returns:
        追加了 keep_mask 字段的 manifest_dict
    """
    print("[2b] 决策子阶段开始")

    tokens = manifest_dict["tokens"]
    comprehension = manifest_dict.get("comprehension", {})
    goal = manifest_dict.get("goal", "")

    if mode not in ("single", "chunked"):
        raise ValueError(f"mode 须为 'single' 或 'chunked'，实际: {mode!r}")
    if mode == "chunked":
        keep_mask = _generate_keep_mask_chunked(tokens, comprehension, goal)
    else:
        keep_mask = _generate_keep_mask(tokens, comprehension, goal)

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

    print(f"[2b] 决策完成")
    print(f"[2b] 保留: {keep_count} | 删除: {cut_count}")

    return manifest_dict


# ============================================================================
# LLM 决策生成
# ============================================================================

def _generate_keep_mask(
    tokens: list[dict],
    comprehension: dict,
    goal: str
) -> list[dict]:
    """调用 LLM 生成 keep_mask

    Args:
        tokens: JSON2 句面列表（index + text）
        comprehension: 2a 理解结果
        goal: 用户目标

    Returns:
        keep_mask 列表 [{"index": int, "keep": bool}, ...]
    """
    print("[2b] 调用 LLM 生成决策")

    prompt = _build_prompt(tokens, comprehension, goal)
    schema = _get_schema()

    response = call_llm_structured(
        prompt=prompt,
        schema=schema,
        temperature=TEMPERATURE,
        enable_reasoning=ENABLE_REASONING
    )

    # 解析 LLM 输出
    llm_decisions = response.get("decisions", [])

    # 构造完整 keep_mask（speech-only）
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
) -> list[dict]:
    """按分块多次调用 LLM，合并为完整 keep_mask。"""
    outline_blocks = comprehension.get("outline_blocks", [])
    cleaned_annotations = comprehension.get("cleaned_annotations", [])
    _validate_dense_cleaned_vs_tokens(tokens, cleaned_annotations)

    if not outline_blocks:
        print("[2b] chunked：outline_blocks 为空，回退为 single 单次调用")
        return _generate_keep_mask(tokens, comprehension, goal)

    partitions = _partition_token_indices_by_blocks(tokens, outline_blocks)
    work = [(b, pos) for b, pos in partitions if pos]

    if not work:
        print("[2b] 警告: 分块后无任何句子，全部默认保留")
        return [{"index": int(tok["index"]), "keep": True} for tok in tokens]

    n_blocks = len(work)
    arc = _narrative_arc_section(work, tokens)
    purpose = comprehension.get("purpose", "")
    schema = _get_schema()
    merged: dict[int, bool] = {}

    for ord1, (block_meta, positions) in enumerate(work, start=1):
        print(f"[2b] chunked LLM 调用 {ord1}/{n_blocks}，本块 {len(positions)} 句")
        prompt = _build_prompt_chunked(
            purpose=purpose,
            goal=goal,
            block_ordinal=ord1,
            n_blocks=n_blocks,
            narrative_arc_section=arc,
            tokens=tokens,
            cleaned_annotations=cleaned_annotations,
            block_positions=positions,
            block_meta=block_meta,
        )
        response = call_llm_structured(
            prompt=prompt,
            schema=schema,
            temperature=TEMPERATURE,
            enable_reasoning=ENABLE_REASONING,
        )
        llm_decisions = response.get("decisions", [])
        allowed = {int(tokens[i]["index"]) for i in positions}
        chunk_map = _merge_chunk_decisions(llm_decisions, allowed)
        for idx, keep in chunk_map.items():
            if idx in merged:
                print(f"[2b] 警告: index {idx} 在多块中重复决策，以后块为准")
            merged[idx] = keep

    synthetic = [
        {"index": int(tok["index"]), "keep": merged.get(int(tok["index"]), True)}
        for tok in tokens
    ]
    return _build_keep_mask_from_llm_decisions(tokens, synthetic)


def _merge_chunk_decisions(
    llm_decisions: list[dict],
    allowed_global_indices: set[int],
) -> dict[int, bool]:
    """只接受属于本块的全文 index；缺省默认保留。"""
    out: dict[int, bool] = {}
    for d in llm_decisions:
        idx = d.get("index")
        if not isinstance(idx, int):
            continue
        if idx not in allowed_global_indices:
            print(f"[2b] 警告: LLM 返回了非本块 index {idx}，已忽略")
            continue
        out[idx] = bool(d.get("keep"))
    for idx in allowed_global_indices:
        if idx not in out:
            print(f"[2b] 警告: 本块 LLM 未覆盖 index {idx}，默认保留")
            out[idx] = True
    return out


def _build_prompt_chunked(
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
) -> str:
    """分块模式下单次 LLM 的 prompt。"""
    goal_line = f"用户目标：{goal}" if goal else "用户目标：无特定目标，提取核心内容"
    lo = int(tokens[block_positions[0]]["index"])
    hi = int(tokens[block_positions[-1]]["index"])
    range_line = f"本块在全文中的 index 范围：{lo}–{hi}（共 {len(block_positions)} 句）"

    speech_lines = []
    for i in block_positions:
        idx = int(tokens[i]["index"])
        text = cleaned_annotations[i]["cleaned_content"]
        speech_lines.append(f"[{idx}] {text}")

    block_summ = _block_summary(block_meta)
    extra = ""
    if block_meta.get("_synthetic_gap"):
        extra = "\n说明：本块为程序根据全文 index 补齐的分区，不在 2a 原始 outline_blocks 中。"

    return f"""{goal_line}

内容主旨：{purpose}

当前决策块：第 {block_ordinal}/{n_blocks} 块
{range_line}
本块摘要：{block_summ if block_summ else "（无）"}{extra}

{narrative_arc_section}

下列仅为「当前块」内的句面（[index] 为全文级 index，与 JSON2 一致）：

{chr(10).join(speech_lines)}

请仅对上述列表中的每一条做出保留/删除决策：
- 保留（keep=true）：与用户目标相关，包含有价值内容
- 删除（keep=false）：无关内容、口头语、重复、语气词等

注意：
1. decisions 中每条必须包含 index（全文级）与 keep，且 index 必须出现在上面的列表中
2. 必须覆盖本块列出的全部 index，不要遗漏

请以 JSON 格式输出。"""


def _build_prompt(
    tokens: list[dict],
    comprehension: dict,
    goal: str
) -> str:
    """构造 2b 决策的 Prompt

    输入元素：
    - 用户目标
    - 2a 的主旨
    - 2a 的稠密消歧标注（唯一文本来源）
    - 2a 的分块信息
    - 所有 speech 标注

    任务：
    对每个 speech 标注判断是否保留
    """
    purpose = comprehension.get("purpose", "")
    cleaned_annotations = comprehension.get("cleaned_annotations", [])
    outline_blocks = comprehension.get("outline_blocks", [])
    _validate_dense_cleaned_vs_tokens(tokens, cleaned_annotations)

    # 构造分块摘要
    if outline_blocks:
        block_lines = [
            f"  块 {i+1} [index {b['start_index']}-{b['end_index']}]: {_block_summary(b)}"
            for i, b in enumerate(outline_blocks)
        ]
        blocks_section = "内容分块：\n" + "\n".join(block_lines)
    else:
        blocks_section = "内容分块：无"

    # 构造句面列表（仅使用稠密消歧文本）
    speech_lines = []
    for i, tok in enumerate(tokens):
        idx = int(tok["index"])
        text = cleaned_annotations[i]["cleaned_content"]
        speech_lines.append(f"[{idx}] {text}")

    speech_text = "\n".join(speech_lines)

    goal_line = f"用户目标：{goal}" if goal else "用户目标：无特定目标，提取核心内容"

    return f"""{goal_line}

内容主旨：{purpose}

{blocks_section}

以下是所有句面（格式：[index] 文字内容）：

{speech_text}

请对每一句做出保留/删除决策：
- 保留（keep=true）：与用户目标相关，包含有价值内容
- 删除（keep=false）：无关内容、口头语、重复、语气词等

注意：
1. 你需要对列表中的每条内容都做决策
2. 输出格式为 decisions 数组，每条包含 index 和 keep 字段
3. 必须覆盖所有条目

请以 JSON 格式输出。"""


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
    """2b 决策的输出 JSON Schema"""
    return {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "description": "对每个 speech 标注的决策列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "description": "对应 tokens[].index"},
                        "keep": {"type": "boolean", "description": "true=保留, false=删除"}
                    },
                    "required": ["index", "keep"]
                }
            }
        },
        "required": ["decisions"]
    }


def _build_keep_mask_from_llm_decisions(
    tokens: list[dict],
    llm_decisions: list[dict]
) -> list[dict]:
    """将 LLM 决策扩展为完整 keep_mask

    Args:
        tokens: JSON2 句面列表
        llm_decisions: LLM 输出决策 [{"index": int, "keep": bool}, ...]

    Returns:
        完整 keep_mask [{"index": int, "keep": bool}, ...]
    """
    # 构造 LLM 决策的 index → keep 映射
    decision_map = {d["index"]: d["keep"] for d in llm_decisions}

    # 构造完整 keep_mask
    keep_mask = []
    for tok in tokens:
        idx = int(tok["index"])
        if idx not in decision_map:
            # LLM 未覆盖此条目，默认保留
            print(f"[2b] 警告: LLM 未覆盖 index {idx}，默认保留")
            keep = True
        else:
            keep = bool(decision_map[idx])
        keep_mask.append({"index": idx, "keep": keep})

    return keep_mask
