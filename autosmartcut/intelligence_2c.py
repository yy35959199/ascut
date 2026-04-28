"""Layer 2 / 2c 审核子阶段

## 职责
验证 2b 决策的合理性：在单次 LLM 调用中，先将模糊的 goal 分解为一组可判真假的
checklist 条目，再逐条对照 keep_mask 做布尔判断。verdict 由程序根据 must 项通过率
计算，不由 LLM 直接输出。

## 设计原理
- LLM 对模糊目标做 pass/not-pass 的二值判断不稳定（采样随机性）。
- 将 goal 分解为离散布尔条件（checklist），每条判断空间远小于原始 goal。
- 同一次调用内先生成 checklist 再逐条判断：两阶段是因果推理链而非独立任务，
  先生成的 checklist token 直接参与后续判断的注意力计算，比拆两次调用更连贯。
- verdict 由程序计算，消除 LLM 可能的 judgments/verdict 矛盾。

## 输入
manifest_dict = {
    "tokens": [{"index": int, "text": str}, ...],
    "comprehension": {
        "purpose": str,
        "outline_blocks": [{"start_index": int, "end_index": int, "summary": str}],
        "cleaned_annotations": [{"annotation_index": int, "cleaned_content": str}]
    },
    "keep_mask": [{"index": int, "keep": bool}, ...],
    "goal": str
}

## 输出
manifest_dict["review_report"] = {
    "round": int,
    "verdict": "pass" | "fix_decision",
    "checklist": [...],
    "judgments": [...],
    "fix_instructions": [...],
    "must_pass_rate": "N/M",
    "token_spent": int
}
"""

from __future__ import annotations

import logging
from typing import Any

from autosmartcut.config import load_config
from autosmartcut.intelligence_llm import build_messages, call_structured

logger = logging.getLogger(__name__)


# ============================================================================
# 主入口
# ============================================================================

def run_2c_review(
    manifest_dict: dict,
    *,
    review_round: int = 0,
) -> dict:
    """2c 审核子阶段。

    根据 ``config.intelligence.two_c_max_review_rounds`` 决定行为：
    - ``max_review_rounds == 0``：占位透传，自动 pass（与旧行为兼容）。
    - ``max_review_rounds >= 1``：调用 LLM 做结构化审核。

    Args:
        manifest_dict: 包含 tokens、comprehension、keep_mask、goal 的工作数据。
        review_round: 当前审核轮次（0-based），由编排层传入。

    Returns:
        追加了 review_report 字段的 manifest_dict。
    """
    cfg = load_config(None).intelligence
    if cfg.two_c_max_review_rounds == 0:
        return _run_2c_passthrough(manifest_dict, review_round)

    return _run_2c_real(manifest_dict, review_round=review_round, cfg=cfg)


# ============================================================================
# 占位透传（max_review_rounds == 0）
# ============================================================================

def _run_2c_passthrough(manifest_dict: dict, review_round: int) -> dict:
    """占位模式：自动生成 pass 报告，不调用 LLM。"""
    logger.info("[2c] 审核子阶段（占位模式，two_c_max_review_rounds=0）")
    manifest_dict["review_report"] = {
        "round": review_round,
        "verdict": "pass",
        "checklist": [],
        "judgments": [],
        "fix_instructions": [],
        "must_pass_rate": "0/0",
        "token_spent": 0,
    }
    logger.info("[2c] 自动通过审核")
    return manifest_dict


# ============================================================================
# 真实审核
# ============================================================================

def _run_2c_real(
    manifest_dict: dict,
    *,
    review_round: int,
    cfg: Any,
) -> dict:
    """真实审核：单次 LLM 调用，两阶段输出（checklist → judgments）。"""
    logger.info("[2c] 审核子阶段开始（轮次 %d）", review_round)

    tokens = manifest_dict["tokens"]
    comprehension = manifest_dict.get("comprehension", {})
    keep_mask = manifest_dict.get("keep_mask", [])
    goal = manifest_dict.get("goal", "")

    if not keep_mask:
        raise ValueError("[2c] keep_mask 为空，无法审核")
    if len(keep_mask) != len(tokens):
        raise ValueError(
            f"[2c] keep_mask 长度不匹配: {len(keep_mask)} != {len(tokens)}"
        )

    prompt = _build_review_prompt(tokens, comprehension, keep_mask, goal)
    schema = _get_review_schema()

    llm_result = call_structured(build_messages(prompt, schema), schema, "review")
    response = llm_result.data

    checklist = response.get("checklist", [])
    judgments = response.get("judgments", [])

    # 校验 judgments 引用的 checklist_index 合法性
    _validate_judgments(checklist, judgments)

    # 程序计算 verdict
    verdict, must_passed, must_total = _compute_verdict(
        checklist, judgments, must_pass_rate=cfg.two_c_must_pass_rate
    )

    # 提取修正指令（仅 fix_decision 时有意义）
    if verdict == "fix_decision":
        fix_instructions = _extract_fix_instructions(checklist, judgments)
        # 如果 fix_instructions 为空（LLM 未给出具体 index），强制 pass
        if not fix_instructions:
            logger.warning(
                "[2c] verdict=fix_decision 但无有效修正指令（evidence_indices 全空），"
                "强制改为 pass"
            )
            verdict = "pass"
            fix_instructions = []
    else:
        fix_instructions = []

    manifest_dict["review_report"] = {
        "round": review_round,
        "verdict": verdict,
        "checklist": checklist,
        "judgments": judgments,
        "fix_instructions": fix_instructions,
        "must_pass_rate": f"{must_passed}/{must_total}",
        "token_spent": int(llm_result.usage.get("total_tokens", 0)),
    }

    logger.info(
        "[2c] 审核完成 verdict=%s must通过=%d/%d checklist=%d条 fixes=%d条",
        verdict,
        must_passed,
        must_total,
        len(checklist),
        len(fix_instructions),
    )
    return manifest_dict


# ============================================================================
# Prompt 构造
# ============================================================================

def _build_review_prompt(
    tokens: list[dict],
    comprehension: dict,
    keep_mask: list[dict],
    goal: str,
) -> str:
    """构造 2c 审核 prompt：上下文 + 按 block 分组的决策状态 + 两阶段任务指令。"""
    purpose = comprehension.get("purpose", "")
    outline_blocks = comprehension.get("outline_blocks", [])
    cleaned_annotations = comprehension.get("cleaned_annotations", [])

    goal_line = f"用户目标：{goal}" if goal else "用户目标：无特定目标，提取核心内容"

    # 区段 1：阶段定位
    stage = (
        "【阶段定位】当前阶段：2c 审核层。\n"
        "上游 2a 已产出主旨与内容分块；2b 已对每句做出 keep/cut 决策。\n"
        "你的任务不是重新做决策，而是验证 2b 的决策是否满足用户目标、"
        "内容是否完整连贯。\n"
    )

    # 区段 2：上下文
    if outline_blocks:
        block_lines = [
            f"  块 {i+1} [index {b['start_index']}-{b['end_index']}]: "
            f"{_block_summary(b)}"
            for i, b in enumerate(outline_blocks)
        ]
        blocks_section = "内容分块（2a 产出）：\n" + "\n".join(block_lines)
    else:
        blocks_section = "内容分块：无"

    # 区段 3：按 block 分组展示决策状态
    annotated_text = _build_annotated_decision_text(
        tokens, comprehension, keep_mask, outline_blocks
    )

    # 区段 4 + 5：两阶段任务指令
    task_instructions = _build_task_instructions()

    return f"""{goal_line}

{stage}内容主旨：{purpose}

{blocks_section}

{annotated_text}

{task_instructions}

请以 JSON 格式输出。"""


def _build_annotated_decision_text(
    tokens: list[dict],
    comprehension: dict,
    keep_mask: list[dict],
    outline_blocks: list[dict],
) -> str:
    """按 outline_block 分组展示每句的决策状态（[✓]=保留 [✗]=删除）。"""
    cleaned_annotations = comprehension.get("cleaned_annotations", [])
    keep_map = {int(e["index"]): bool(e["keep"]) for e in keep_mask}

    # 使用 cleaned_annotations 的文本（如果有且与 tokens 等长），否则用 tokens
    use_cleaned = (
        isinstance(cleaned_annotations, list)
        and len(cleaned_annotations) == len(tokens)
    )

    def _get_text(pos: int) -> str:
        if use_cleaned and pos < len(cleaned_annotations):
            return str(cleaned_annotations[pos].get("cleaned_content", ""))
        return str(tokens[pos].get("text", ""))

    lines: list[str] = ["【当前决策状态】（[✓]=保留  [✗]=删除）"]

    if outline_blocks:
        # 按 block 分组
        assigned = [False] * len(tokens)
        for bi, block in enumerate(outline_blocks):
            s = int(block.get("start_index", 0))
            e = int(block.get("end_index", 0))
            summ = _block_summary(block)
            lines.append(
                f"\n── 块 {bi+1} [index {s}-{e}]: {summ} ──"
            )
            for pos, tok in enumerate(tokens):
                if assigned[pos]:
                    continue
                idx = int(tok["index"])
                if s <= idx <= e:
                    assigned[pos] = True
                    mark = "✓" if keep_map.get(idx, True) else "✗"
                    lines.append(f"[{mark}] [{idx}] {_get_text(pos)}")

        # 未分配的句子
        remainder = [
            pos for pos in range(len(tokens)) if not assigned[pos]
        ]
        if remainder:
            lines.append("\n── 未归入分块的句子 ──")
            for pos in remainder:
                idx = int(tokens[pos]["index"])
                mark = "✓" if keep_map.get(idx, True) else "✗"
                lines.append(f"[{mark}] [{idx}] {_get_text(pos)}")
    else:
        # 无分块，全部平铺
        for pos, tok in enumerate(tokens):
            idx = int(tok["index"])
            mark = "✓" if keep_map.get(idx, True) else "✗"
            lines.append(f"[{mark}] [{idx}] {_get_text(pos)}")

    # 统计
    keep_count = sum(1 for e in keep_mask if e.get("keep") is True)
    cut_count = sum(1 for e in keep_mask if e.get("keep") is False)
    lines.append(f"\n统计：保留 {keep_count} 句 | 删除 {cut_count} 句 | 共 {len(tokens)} 句")

    return "\n".join(lines)


def _build_task_instructions() -> str:
    """两阶段任务指令：生成 checklist → 逐条判断。"""
    return r"""【第一步：生成审核检查清单 checklist】
（此步必须优先完成。在 checklist 数组的所有条目完整输出之前，禁止开始第二步的任何判断。
先把所有 checklist 条目全部列出，确认无遗漏后，再进入第二步。）

根据「用户目标」和「内容分块」，列出一组具体的、可判断真假的审核条件。

生成规则：
- 从用户目标出发（首要）：目标要求保留什么？删除什么？有什么隐含期望？
  将这些拆解为具体条件（如"是否保留了关于X的核心解释"）。
- 从内容分块出发（辅助）：每个 block 的 summary 是否在保留内容中有足够支撑？
  与 goal 高度相关的 block 生成 must 条件；与 goal 明确无关的 block 可不生成条件。
- 补充结构性条件：是否存在连续大段删除导致的语义断裂？
  是否存在明显重复内容被同时保留？
- 每条标注 priority：must（必须通过）或 optional（建议通过）。
- 每条标注 source：条件来源，"goal"（来自用户目标）、"block_N"（来自第 N 块）、
  "structural"（结构性检查）。
- 条目数量：5-12 条为宜，不要过细也不要过粗。

【第二步：逐条判断】
（禁止依赖记忆或整体印象。必须从头重新逐句阅读上方「当前决策状态」中的原文，
对每条 checklist 条目，在原文中逐句扫描寻找证据，不得跳过任何句子。）

拿着你在第一步生成的 checklist，逐条对照上方的决策状态做判断。

判断规则：
- 对每条 checklist，在标记为 [✓] 的保留句中寻找支撑证据。
- 必须给出 evidence_indices：哪些具体 index 的保留句支撑了这条判断。
  若 pass=true，列出支撑该条件的保留句 index。
  若 pass=false，列出应该保留但被标记为 [✗] 的句子 index。
- 不允许无证据的判断——如果找不到具体句子支撑，则该条 pass=false。
- judgments 数组的每一项通过 checklist_index（0-based）与 checklist 对应，
  必须覆盖 checklist 的每一条，不得遗漏。"""


def _block_summary(block: dict) -> str:
    """兼容 2a 的 summary / topic 字段。"""
    s = block.get("summary")
    if isinstance(s, str) and s.strip():
        return s.strip()
    t = block.get("topic")
    if isinstance(t, str) and t.strip():
        return t.strip()
    return "（无摘要）"


# ============================================================================
# 输出 Schema
# ============================================================================

def _get_review_schema() -> dict:
    """2c 审核的输出 JSON Schema。verdict 不在此处，由程序计算。"""
    return {
        "type": "object",
        "properties": {
            "checklist": {
                "type": "array",
                "description": "审核检查清单（第一步生成）",
                "items": {
                    "type": "object",
                    "properties": {
                        "item": {
                            "type": "string",
                            "description": "审核条件的自然语言描述",
                        },
                        "source": {
                            "type": "string",
                            "description": "条件来源：goal / block_N / structural",
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["must", "optional"],
                            "description": "优先级",
                        },
                    },
                    "required": ["item", "source", "priority"],
                },
            },
            "judgments": {
                "type": "array",
                "description": "逐条判断结果（第二步生成）",
                "items": {
                    "type": "object",
                    "properties": {
                        "checklist_index": {
                            "type": "integer",
                            "description": "对应 checklist 数组下标（0-based）",
                        },
                        "pass": {
                            "type": "boolean",
                            "description": "该条件是否通过",
                        },
                        "evidence_indices": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": (
                                "pass=true 时为支撑句 index；"
                                "pass=false 时为应保留但被删的句 index"
                            ),
                        },
                        "note": {
                            "type": "string",
                            "description": "一句话说明判断理由",
                        },
                    },
                    "required": [
                        "checklist_index",
                        "pass",
                        "evidence_indices",
                        "note",
                    ],
                },
            },
        },
        "required": ["checklist", "judgments"],
    }


# ============================================================================
# 程序层后处理
# ============================================================================

def _validate_judgments(
    checklist: list[dict],
    judgments: list[dict],
) -> None:
    """校验 judgments 引用的 checklist_index 合法性；不合法的记录警告但不抛异常。"""
    n = len(checklist)
    for j in judgments:
        ci = j.get("checklist_index")
        if not isinstance(ci, int) or ci < 0 or ci >= n:
            logger.warning(
                "[2c] judgment 引用了无效 checklist_index=%s（checklist 长度=%d），"
                "将被忽略",
                ci,
                n,
            )


def _compute_verdict(
    checklist: list[dict],
    judgments: list[dict],
    *,
    must_pass_rate: float = 1.0,
) -> tuple[str, int, int]:
    """程序计算 verdict。

    Returns:
        (verdict, must_passed, must_total)
    """
    must_indices = [
        i for i, c in enumerate(checklist) if c.get("priority") == "must"
    ]
    if not must_indices:
        logger.info("[2c] checklist 中无 must 项，直接 pass")
        return "pass", 0, 0

    judgment_map: dict[int, dict] = {}
    for j in judgments:
        ci = j.get("checklist_index")
        if isinstance(ci, int):
            judgment_map[ci] = j

    must_passed = sum(
        1
        for i in must_indices
        if judgment_map.get(i, {}).get("pass", False)
    )
    rate = must_passed / len(must_indices)
    verdict = "pass" if rate >= must_pass_rate else "fix_decision"
    return verdict, must_passed, len(must_indices)


def _extract_fix_instructions(
    checklist: list[dict],
    judgments: list[dict],
) -> list[dict]:
    """从未通过的 must 项提取修正指令。

    仅提取 evidence_indices 非空的项——没有具体 index 的修正指令对 2b 无意义。
    """
    judgment_map: dict[int, dict] = {}
    for j in judgments:
        ci = j.get("checklist_index")
        if isinstance(ci, int):
            judgment_map[ci] = j

    fixes: list[dict] = []
    for i, c in enumerate(checklist):
        if c.get("priority") != "must":
            continue
        j = judgment_map.get(i)
        if j is None:
            continue
        if j.get("pass", False):
            continue
        evidence = j.get("evidence_indices", [])
        if not evidence:
            continue
        fixes.append({
            "requirement": c.get("item", ""),
            "missing_indices": evidence,
            "note": j.get("note", ""),
        })
    return fixes
