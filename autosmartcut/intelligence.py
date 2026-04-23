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
from autosmartcut.intelligence_2a import run_2a_comprehension
from autosmartcut.intelligence_2b import run_2b_decision
from autosmartcut.intelligence_2c import run_2c_review
from autosmartcut.intelligence_2d import run_2d_human_review
from autosmartcut.manifest_io import (
    load_manifest,
    save_manifest,
    strip_volatile_fields,
    touch_layer_status,
    write_l2_checkpoint,
)

logger = logging.getLogger(__name__)


def compute_l2_layer_result(
    data: dict[str, Any],
    goal: str,
    *,
    auto: bool = False,
    verbose_log: bool = False,
    two_b_mode: str = "single",
    on_phase_save: Callable[[str, dict[str, Any]], None] | None = None,
    on_after_2b_round: Callable[[dict[str, Any], int], None] | None = None,
) -> dict[str, Any]:
    """纯计算：跑完 2a→2b/2c 循环→2d，返回写入 ``current`` 所需字段；不写盘。

    ``on_phase_save``：与 ``run_2a_comprehension`` 一致；双轨并行时应传 ``None``，避免写主清单。
    ``on_after_2b_round``：每轮 2b 后回调 ``(manifest_dict, review_round)``；串行路径用于写检查点。
    """
    if two_b_mode not in ("single", "chunked"):
        raise ValueError(f"two_b_mode 须为 'single' 或 'chunked'，实际: {two_b_mode!r}")
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
            manifest_dict = run_2d_human_review(manifest_dict)

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
        choices=["single", "chunked"],
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
