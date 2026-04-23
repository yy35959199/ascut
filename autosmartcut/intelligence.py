"""Layer 2 智能层 - 流程编排主入口

## 职责
- 提供统一入口 run_intelligence_layer()
- 按顺序调用 2a → 2b → 2c → 2d
- 读写 **timeline_manifest.json**：内存中组 ``tokens[]``，写回 ``current``

## 核心不变量
- ``tokens[i].index == i``；``keep_mask`` 与 ``tokens`` 等长且 index 对齐
"""

import copy
import logging
from datetime import datetime
from collections.abc import Callable
from pathlib import Path
from typing import Any

from autosmartcut.annotation_tokens import tokens_from_annotations
from autosmartcut.log import (
    log_lazy_json,
    log_stage,
    log_stage_result,
    setup_logging_for_manifest,
)
from autosmartcut.intelligence_2a import run_2a_comprehension, run_2a_comprehension_reflow
from autosmartcut.intelligence_2b import run_2b_decision
from autosmartcut.intelligence_2c import run_2c_review
from autosmartcut.intelligence_2d import run_2d_human_review
from autosmartcut.intelligence_2d_core import Signal
from autosmartcut.intelligence_llm import call_llm_structured
from autosmartcut.manifest_io import (
    load_manifest,
    save_manifest,
    strip_volatile_fields,
    touch_layer_status,
    write_l2_checkpoint,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 2d 回流辅助函数
# ============================================================================

def _check_correction_affects_purpose(
    purpose: str,
    old_word: str,
    new_word: str,
) -> bool:
    """轻量 LLM 判断：F2 纠错是否影响 purpose 的准确性。

    使用非 reasoner 模型（enable_reasoning=False, temperature=0.1），
    单次调用，判断空间为 true/false。

    Preconditions:
        - purpose 非空
        - old_word 和 new_word 非空

    Postconditions:
        - 返回 bool，True 表示需要重跑 R2 更新 purpose
    """
    prompt = (
        f"当前内容主旨：{purpose}\n\n"
        f"ASR 识别纠错：「{old_word}」→「{new_word}」\n\n"
        f"请判断：这个纠错是否会影响上述主旨的准确性？\n"
        f"仅回答 true 或 false，不需要解释。"
    )
    schema = {
        "type": "object",
        "properties": {
            "affects_purpose": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": ["affects_purpose"],
    }
    result = call_llm_structured(
        prompt=prompt,
        schema=schema,
        temperature=0.1,
        enable_reasoning=False,
    )
    return bool(result.get("affects_purpose", False))


def _run_2b_2c_cycle(
    manifest_dict: dict[str, Any],
    *,
    two_b_mode: str = "single",
    on_after_2b_round: Callable[[dict[str, Any], int], None] | None = None,
) -> dict[str, Any]:
    """封装 2b → 2c 审核循环（含修正重跑），供回流和主流程复用。

    Preconditions:
        - manifest_dict 包含 tokens、comprehension
    Postconditions:
        - manifest_dict 包含 keep_mask 和 review_report
    """
    from autosmartcut.config import load_config as _load_cfg

    _intel_cfg = _load_cfg(None).intelligence
    max_review_rounds = _intel_cfg.two_c_max_review_rounds
    review_fixes: list[dict] | None = None

    for review_round in range(max_review_rounds + 1):
        is_fix_rerun = review_round > 0

        with log_stage(
            "l2.2b_decision",
            mode=two_b_mode,
            review_round=review_round,
            is_fix_rerun=is_fix_rerun,
        ):
            manifest_dict = run_2b_decision(
                manifest_dict,
                mode=two_b_mode,
                review_fixes=review_fixes if is_fix_rerun else None,
            )

        if on_after_2b_round is not None:
            on_after_2b_round(manifest_dict, review_round)

        with log_stage("l2.2c_review", review_round=review_round):
            manifest_dict = run_2c_review(
                manifest_dict, review_round=review_round,
            )

        report = manifest_dict.get("review_report", {})
        verdict = report.get("verdict", "pass")

        if verdict == "pass":
            logger.info("[L2] 2c 审核通过（轮次 %d）", review_round)
            break

        if review_round < max_review_rounds:
            review_fixes = report.get("fix_instructions", [])
            logger.info(
                "[L2] 2c 审核未通过，修正轮 %d/%d（%d 条修正指令）",
                review_round + 1,
                max_review_rounds,
                len(review_fixes) if review_fixes else 0,
            )
        else:
            logger.info(
                "[L2] 达到最大审核轮次 %d，强制通过交给 2d 人工兜底",
                max_review_rounds,
            )
            report["verdict"] = "pass"
            manifest_dict["review_report"] = report

    return manifest_dict


def _reflow_through_2a(
    manifest_dict: dict[str, Any],
    feedback_type: str,
    feedback_payload: dict[str, Any],
    *,
    two_b_mode: str = "single",
    on_phase_save: Callable | None = None,
    on_after_2b_round: Callable | None = None,
) -> dict[str, Any]:
    """F1/F2 回流：重跑 2a → 2b → 2c。

    F1 (purpose_drift):
        - 2a 进入 R2 refinement 模式，注入用户反馈更新 purpose + outline_blocks
        - 然后 2b → 2c

    F2 (keyword_error):
        - 追加纠错到 cleaned_annotations（程序步骤，无 LLM）
        - 轻量 LLM 判断纠错是否影响 purpose
        - 若影响：追加 purpose_drift 模式重跑
        - 然后 2b → 2c
    """
    if feedback_type == "f1_purpose_drift":
        manifest_dict = run_2a_comprehension_reflow(
            manifest_dict,
            reflow_mode="purpose_drift",
            feedback_text=feedback_payload["text"],
            on_phase_save=on_phase_save,
        )
    elif feedback_type == "f2_keyword_error":
        # Step 1: 追加纠错 + 重建 cleaned_annotations（程序步骤，无 LLM）
        manifest_dict = run_2a_comprehension_reflow(
            manifest_dict,
            reflow_mode="keyword_correction",
            correction=feedback_payload,  # {"index": int, "old": str, "new": str}
            on_phase_save=on_phase_save,
        )
        # Step 2: 轻量 LLM 判断纠错是否影响 purpose
        affects_purpose = _check_correction_affects_purpose(
            purpose=manifest_dict["comprehension"]["purpose"],
            old_word=feedback_payload["old"],
            new_word=feedback_payload["new"],
        )
        # Step 3: 若影响 purpose，追加 R2 精化
        if affects_purpose:
            logger.info(
                "[2d reflow] F2 纠错影响 purpose，追加 R2 精化：%s → %s",
                feedback_payload["old"],
                feedback_payload["new"],
            )
            manifest_dict = run_2a_comprehension_reflow(
                manifest_dict,
                reflow_mode="purpose_drift",
                feedback_text=(
                    f"关键词已纠正：「{feedback_payload['old']}」→「{feedback_payload['new']}」，"
                    f"请基于纠正后的理解更新主旨。"
                ),
                on_phase_save=on_phase_save,
            )

    # 重跑 2b → 2c
    manifest_dict = _run_2b_2c_cycle(
        manifest_dict,
        two_b_mode=two_b_mode,
        on_after_2b_round=on_after_2b_round,
    )

    return manifest_dict


def _reflow_through_2b(
    manifest_dict: dict[str, Any],
    feedback_type: str,
    feedback_payload: dict[str, Any],
    *,
    two_b_mode: str = "single",
    on_after_2b_round: Callable | None = None,
) -> dict[str, Any]:
    """F3 回流：跳过 2a，注入用户意见到 2b prompt，重跑 2b → 2c。

    Preconditions:
        - feedback_type 为 "f3_selection_opinion"
        - manifest_dict 包含有效的 comprehension（不变）

    Postconditions:
        - comprehension 不变
        - keep_mask 已由 2b 重新生成（注入了用户选择意见）
        - review_report 已由 2c 重新生成
    """
    # 将用户意见注入 manifest 供 2b prompt 构造时读取
    manifest_dict["_selection_opinion"] = feedback_payload["text"]

    manifest_dict = _run_2b_2c_cycle(
        manifest_dict,
        two_b_mode=two_b_mode,
        on_after_2b_round=on_after_2b_round,
    )

    # 清理临时字段
    manifest_dict.pop("_selection_opinion", None)

    return manifest_dict


def _run_2d_with_reflow(
    manifest_dict: dict[str, Any],
    *,
    max_2d_reflows: int = 3,
    two_b_mode: str = "single",
    on_phase_save: Callable | None = None,
    on_after_2b_round: Callable | None = None,
) -> dict[str, Any]:
    """2d 交互 + 回流循环。

    Preconditions:
        - manifest_dict 已完成 2a → 2b → 2c
        - max_2d_reflows >= 0

    Postconditions:
        - 返回的 manifest_dict 包含最终 keep_mask
        - 回流次数 <= max_2d_reflows
    """
    # 延迟导入 run_2d_interactive：TUI shell 模块可能尚未实现
    try:
        from autosmartcut.intelligence_2d_shell import run_2d_interactive
    except ImportError:
        logger.info("[2d] TUI shell 不可用，回退到旧版 run_2d_human_review")

        def run_2d_interactive(md: dict) -> tuple[dict, Signal]:
            md = run_2d_human_review(md)
            return md, Signal.DONE

    reflow_count = 0

    while True:
        manifest_dict, signal = run_2d_interactive(manifest_dict)

        if signal == Signal.DONE:
            break

        if signal == Signal.QUIT:
            raise KeyboardInterrupt("用户取消")

        if signal in (Signal.REFLOW_2A, Signal.REFLOW_2B):
            if reflow_count >= max_2d_reflows:
                logger.warning(
                    "[L2] 2d 回流已达上限 %d，强制进入确认模式",
                    max_2d_reflows,
                )
                # 达到上限后不执行回流，继续循环让用户确认
                continue

            reflow_count += 1
            ctx = manifest_dict.pop("_reflow_context", {})
            ft = ctx.get("feedback_type", "")
            payload = ctx.get("feedback_payload", {})

            if signal == Signal.REFLOW_2A:
                manifest_dict = _reflow_through_2a(
                    manifest_dict, ft, payload,
                    two_b_mode=two_b_mode,
                    on_phase_save=on_phase_save,
                    on_after_2b_round=on_after_2b_round,
                )
            else:  # REFLOW_2B
                manifest_dict = _reflow_through_2b(
                    manifest_dict, ft, payload,
                    two_b_mode=two_b_mode,
                    on_after_2b_round=on_after_2b_round,
                )

    return manifest_dict


def compute_l2_layer_result(
    data: dict[str, Any],
    goal: str,
    *,
    auto: bool = False,
    verbose_log: bool = False,
    two_b_mode: str = "single",
    max_2d_reflows: int | None = None,
    on_phase_save: Callable[[str, dict[str, Any]], None] | None = None,
    on_after_2b_round: Callable[[dict[str, Any], int], None] | None = None,
) -> dict[str, Any]:
    """纯计算：跑完 2a→2b/2c 循环→2d，返回写入 ``current`` 所需字段；不写盘。

    ``on_phase_save``：与 ``run_2a_comprehension`` 一致；双轨并行时应传 ``None``，避免写主清单。
    ``on_after_2b_round``：每轮 2b 后回调 ``(manifest_dict, review_round)``；串行路径用于写检查点。
    ``max_2d_reflows``：2d 回流上限；None 时从 IntelligenceConfig 读取默认值。
    """
    if two_b_mode not in ("single", "block"):
        raise ValueError(f"two_b_mode 须为 'single' 或 'block'，实际: {two_b_mode!r}")
    annotations = data.get("annotations")
    if not isinstance(annotations, list) or len(annotations) == 0:
        raise ValueError("清单缺少非空 annotations[]，无法运行 L2")

    goal_use = goal.strip() if goal.strip() else str(data.get("goal", ""))

    with log_stage(
        "l2.tokens_from_annotations",
        annotation_count=len(annotations),
    ):
        tokens = tokens_from_annotations(annotations)

    manifest_dict: dict[str, Any] = {
        "tokens": tokens,
        "goal": goal_use,
        "source": str(data.get("source", "")),
        "language": str(data.get("language", "")),
        "raw_text": str(data.get("raw_text", "")),
    }

    from autosmartcut.config import load_config as _load_cfg

    _intel_cfg = _load_cfg(None).intelligence
    max_review_rounds = _intel_cfg.two_c_max_review_rounds
    if max_2d_reflows is None:
        max_2d_reflows = _intel_cfg.two_d_max_reflows
    review_fixes: list[dict] | None = None

    with log_stage("l2.2a_comprehension", token_count=len(tokens)):
        manifest_dict = run_2a_comprehension(
            manifest_dict, on_phase_save=on_phase_save
        )

    for review_round in range(max_review_rounds + 1):
        is_fix_rerun = review_round > 0

        with log_stage(
            "l2.2b_decision",
            mode=two_b_mode,
            review_round=review_round,
            is_fix_rerun=is_fix_rerun,
        ):
            manifest_dict = run_2b_decision(
                manifest_dict,
                mode=two_b_mode,
                review_fixes=review_fixes if is_fix_rerun else None,
            )

        if on_after_2b_round is not None:
            on_after_2b_round(manifest_dict, review_round)

        with log_stage("l2.2c_review", review_round=review_round):
            manifest_dict = run_2c_review(
                manifest_dict, review_round=review_round
            )

        report = manifest_dict.get("review_report", {})
        verdict = report.get("verdict", "pass")

        if verdict == "pass":
            logger.info("[L2] 2c 审核通过（轮次 %d）", review_round)
            break

        if review_round < max_review_rounds:
            review_fixes = report.get("fix_instructions", [])
            logger.info(
                "[L2] 2c 审核未通过，修正轮 %d/%d（%d 条修正指令）",
                review_round + 1,
                max_review_rounds,
                len(review_fixes) if review_fixes else 0,
            )
        else:
            logger.info(
                "[L2] 达到最大审核轮次 %d，强制通过交给 2d 人工兜底",
                max_review_rounds,
            )
            report["verdict"] = "pass"
            manifest_dict["review_report"] = report

    if auto:
        logger.info("[L2] auto 模式，跳过 2d 人工审阅")
        manifest_dict.setdefault("human_feedback_history", []).append(
            {
                "round": 0,
                "verdict": "confirm",
                "overrides": [],
                "feedback": "",
                "timestamp": datetime.now().isoformat(),
            }
        )
    else:
        with log_stage("l2.2d_human_review"):
            manifest_dict = _run_2d_with_reflow(
                manifest_dict,
                max_2d_reflows=max_2d_reflows,
                two_b_mode=two_b_mode,
                on_phase_save=on_phase_save,
                on_after_2b_round=on_after_2b_round,
            )

    keep_mask = manifest_dict.get("keep_mask", [])
    if not keep_mask:
        raise ValueError("智能层未生成 keep_mask")
    if len(keep_mask) != len(tokens):
        raise ValueError(
            f"keep_mask 长度不匹配: {len(keep_mask)} != {len(tokens)}"
        )
    for i, entry in enumerate(keep_mask):
        if "index" not in entry:
            raise ValueError(f"keep_mask[{i}] 缺少 index 字段")
        if "keep" not in entry:
            raise ValueError(f"keep_mask[{i}] 缺少 keep 字段")
        if entry["index"] != i:
            raise ValueError(
                f"keep_mask[{i}] 的 index 不匹配: 期望 {i}, 实际 {entry['index']}"
            )

    out: dict[str, Any] = {
        "comprehension": copy.deepcopy(manifest_dict.get("comprehension", {})),
        "keep_mask": copy.deepcopy(keep_mask),
        "goal": goal_use,
    }
    if manifest_dict.get("review_report"):
        out["review_report"] = copy.deepcopy(manifest_dict["review_report"])
    if manifest_dict.get("human_feedback_history"):
        out["human_feedback_history"] = copy.deepcopy(
            manifest_dict["human_feedback_history"]
        )
    return out


def run_intelligence_layer(
    manifest_path: Path,
    goal: str = "",
    *,
    auto: bool = False,
    verbose_log: bool = False,
    two_b_mode: str = "single",
) -> None:
    """Layer 2 主入口：清单 → 更新 ``current``（comprehension + keep_mask）。"""
    mp = manifest_path.resolve()
    setup_logging_for_manifest(mp, verbose=verbose_log)
    logger.info("[L2] 智能层开始 清单=%s", mp)
    logger.info("[L2] 2b 模式: %s", two_b_mode)

    data = load_manifest(mp)
    annotations = data.get("annotations")
    if not isinstance(annotations, list) or len(annotations) == 0:
        raise ValueError("清单缺少非空 annotations[]，无法运行 L2")

    goal_use = goal.strip() if goal.strip() else str(data.get("goal", ""))
    if goal_use:
        logger.info("[L2] 目标: %s", goal_use)

    def _on_2a_phase_save(phase: str, payload: dict[str, Any]) -> None:
        write_l2_checkpoint(data, mp, phase, payload)

    def _on_after_2b_round(manifest_dict: dict[str, Any], review_round: int) -> None:
        cur0 = data.setdefault("current", {})
        if not isinstance(cur0, dict):
            data["current"] = {}
            cur0 = data["current"]
        cur0["comprehension"] = copy.deepcopy(
            manifest_dict.get("comprehension", {})
        )
        cur0["keep_mask"] = copy.deepcopy(manifest_dict.get("keep_mask", []))
        km = cur0["keep_mask"]
        n_keep = sum(
            1 for e in km if isinstance(e, dict) and e.get("keep") is True
        )
        write_l2_checkpoint(
            data,
            mp,
            f"2b_r{review_round}",
            {"keep_true": n_keep, "keep_total": len(km)},
        )

    try:
        result = compute_l2_layer_result(
            data,
            goal,
            auto=auto,
            two_b_mode=two_b_mode,
            on_phase_save=_on_2a_phase_save,
            on_after_2b_round=_on_after_2b_round,
        )
    except KeyboardInterrupt:
        logger.warning("[L2] 用户中断")
        raise
    except Exception as e:
        logger.error("[L2] 执行失败: %s", e)
        raise

    write_l2_checkpoint(
        data,
        mp,
        "2c",
        {
            "verdict": result.get("review_report", {}).get("verdict", "pass"),
        },
    )

    keep_mask = result["keep_mask"]

    cur = data.setdefault("current", {})
    if not isinstance(cur, dict):
        data["current"] = {}
        cur = data["current"]
    cur["comprehension"] = result.get("comprehension", {})
    cur["keep_mask"] = keep_mask
    if result.get("review_report"):
        cur["review_report"] = result["review_report"]
    if result.get("human_feedback_history"):
        cur["human_feedback_history"] = result["human_feedback_history"]
    data["goal"] = result.get("goal", goal_use)

    strip_volatile_fields(data)
    touch_layer_status(data, "l2")
    save_manifest(mp, data, atomic=True)

    keep_count = sum(1 for e in keep_mask if e["keep"] is True)
    log_lazy_json("L2", "keep_mask 完整输出", lambda: keep_mask)
    log_lazy_json(
        "L2",
        "comprehension 完整输出",
        lambda: manifest_dict.get("comprehension", {}),
    )
    log_stage_result(
        "l2.output",
        summary=f"保留 {keep_count}/{len(keep_mask)} 句 manifest={mp}",
    )
    logger.info(
        "[L2] 完成 保留 %d/%d 句 → %s",
        keep_count,
        len(keep_mask),
        mp,
    )


def main() -> None:
    """命令行入口：``python -m autosmartcut.intelligence --manifest <path> [--goal ...]``"""
    import argparse
    import sys

    p = argparse.ArgumentParser(description="Layer 2：更新 timeline_manifest.json")
    p.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="timeline_manifest.json（须含 annotations[]）",
    )
    p.add_argument("--goal", type=str, default="", help="智能层目标")
    p.add_argument(
        "--auto",
        action="store_true",
        help="跳过 2d 人工审阅",
    )
    p.add_argument("--verbose", action="store_true", help="DEBUG 日志")
    p.add_argument(
        "--two-b-mode",
        type=str,
        choices=["single", "block"],
        default="single",
    )
    args = p.parse_args()

    try:
        run_intelligence_layer(
            args.manifest,
            args.goal,
            auto=args.auto,
            verbose_log=args.verbose,
            two_b_mode=args.two_b_mode,
        )
    except Exception as e:
        logger.exception("错误: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
