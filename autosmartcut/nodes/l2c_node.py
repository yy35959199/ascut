"""L2cNode — 审核子阶段节点。

薄包装 intelligence_2c.run_2c_review()。
直接操作 ctx.manifest（run_2c_review 就地修改并返回 manifest_dict）。
verdict 由程序计算（must 项通过率 >= two_c_must_pass_rate）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from autosmartcut.annotation_tokens import tokens_from_annotations
from autosmartcut.pipeline_events import ProgressEvent
from autosmartcut.pipeline_models import L2cOutput, StageResult, StageStatus

if TYPE_CHECKING:
    from autosmartcut.config import AppConfig
    from autosmartcut.pipeline_models import StageContext

logger = logging.getLogger(__name__)


class L2cNode:
    """L2C：审核子阶段（checklist 生成 + 逐条判断）。"""

    id = "l2c_review"
    reads = frozenset({"keep_mask", "comprehension", "annotations_l1a", "goal"})
    writes = frozenset({"review_report"})
    phase = 2
    resumable = True

    def __init__(self, config: "AppConfig") -> None:
        self._config = config

    async def run(self, ctx: "StageContext") -> StageResult:
        """薄包装 intelligence_2c.run_2c_review()。"""
        from autosmartcut.intelligence_2c import run_2c_review

        manifest = ctx.manifest
        params = manifest.get("_params", {})
        review_round = int(params.get("review_round", 0))

        annotations_l1a = manifest.get("annotations_l1a", [])
        keep_mask = manifest.get("keep_mask", [])

        if not annotations_l1a:
            return StageResult(
                status=StageStatus.FAILED,
                summary="annotations_l1a 为空，无法执行 L2C 审核",
                error=ValueError("annotations_l1a 为空"),
            )

        if not keep_mask:
            return StageResult(
                status=StageStatus.FAILED,
                summary="keep_mask 为空，无法执行 L2C 审核",
                error=ValueError("keep_mask 为空"),
            )

        # 确保 tokens 存在（从 annotations_l1a 派生）
        if "tokens" not in manifest:
            try:
                manifest["tokens"] = tokens_from_annotations(annotations_l1a)
            except Exception as e:
                logger.warning("[L2cNode] tokens_from_annotations 失败: %s", e)
                manifest["tokens"] = [
                    {"index": int(ann.get("index", i)), "text": str(ann.get("content", ""))}
                    for i, ann in enumerate(annotations_l1a)
                ]

        ctx.emit(ProgressEvent(
            node_id=self.id,
            message=f"审核中（轮次 {review_round}）：生成 checklist 并逐条判断...",
        ))

        try:
            # run_2c_review 直接修改 manifest 并返回
            await asyncio.to_thread(
                run_2c_review,
                manifest,
                review_round=review_round,
            )
        except Exception as e:
            logger.exception("[L2cNode] run_2c_review 失败: %s", e)
            return StageResult(
                status=StageStatus.FAILED,
                summary=f"L2C 审核失败: {e}",
                error=e,
            )

        review_report = manifest.get("review_report", {})
        verdict = review_report.get("verdict", "pass")
        must_pass_rate = review_report.get("must_pass_rate", "0/0")
        fix_count = len(review_report.get("fix_instructions", []))

        ctx.emit(ProgressEvent(
            node_id=self.id,
            message=f"L2C 完成：verdict={verdict} must通过率={must_pass_rate} 修正={fix_count}条",
        ))

        return StageResult(
            status=StageStatus.SUCCESS,
            summary=f"verdict={verdict} must_pass_rate={must_pass_rate} fix_count={fix_count}",
        )

    def summarize(self, manifest: dict) -> L2cOutput:
        review_report = manifest.get("review_report", {})
        return L2cOutput(
            verdict=review_report.get("verdict", "pass"),
            must_pass_rate=review_report.get("must_pass_rate", "0/0"),
            fix_count=len(review_report.get("fix_instructions", [])),
        )
