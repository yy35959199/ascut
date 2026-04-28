"""L2cNode — 审核子阶段节点。

薄包装 intelligence_2c.run_2c_review()。
直接操作 ctx.manifest（run_2c_review 就地修改并返回 manifest_dict）。
verdict 由程序计算（must 项通过率 >= two_c_must_pass_rate）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from autosmartcut.nodes.l2.annotation_tokens import tokens_from_annotations
from autosmartcut.pipeline.pipeline_events import ProgressEvent
from autosmartcut.pipeline.pipeline_models import L2cOutput, StageResult, StageStatus
from autosmartcut.nodes.l2.llm_progress import make_on_chunk

if TYPE_CHECKING:
    from autosmartcut.config import AppConfig
    from autosmartcut.pipeline.pipeline_models import StageContext

logger = logging.getLogger(__name__)


class L2cNode:
    """L2C：审核子阶段（checklist 生成 + 逐条判断）。"""

    id = "l2c_review"
    reads = frozenset({"keep_mask", "comprehension", "annotations", "goal"})
    writes = frozenset({"review_report"})
    phase = 2
    resumable = True

    def __init__(self, config: "AppConfig") -> None:
        self._config = config

    async def run(self, ctx: "StageContext") -> StageResult:
        """薄包装 intelligence_2c.run_2c_review()。"""
        from autosmartcut.nodes.l2.intelligence_2c import run_2c_review

        manifest = ctx.manifest
        params = ctx.params
        review_round = int(params.get("review_round", 0))

        annotations = manifest.get("annotations", [])
        keep_mask = manifest.get("keep_mask", [])

        if not annotations:
            return StageResult(
                status=StageStatus.FAILED,
                summary="annotations 为空，无法执行 L2C 审核",
                error=ValueError("annotations 为空"),
            )

        if not keep_mask:
            return StageResult(
                status=StageStatus.FAILED,
                summary="keep_mask 为空，无法执行 L2C 审核",
                error=ValueError("keep_mask 为空"),
            )

        # 确保 tokens 存在（从 annotations 派生）
        if "tokens" not in manifest:
            try:
                manifest["tokens"] = tokens_from_annotations(annotations)
            except Exception as e:
                logger.warning("[L2cNode] tokens_from_annotations 失败: %s", e)
                manifest["tokens"] = [
                    {"index": int(ann.get("index", i)), "text": str(ann.get("content", ""))}
                    for i, ann in enumerate(annotations)
                ]

        ctx.emit(ProgressEvent(node_id=self.id, phase="review_start", payload={
            "review_round": review_round,
        }))

        try:
            # run_2c_review 直接修改 manifest 并返回
            await asyncio.to_thread(
                run_2c_review,
                manifest,
                review_round=review_round,
                on_chunk=make_on_chunk(ctx.emit, self.id),
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

        ctx.emit(ProgressEvent(node_id=self.id, phase="review_done", payload={
            "elapsed_sec": 0.0,
            "verdict": verdict,
            "must_pass_rate": must_pass_rate,
            "fix_count": fix_count,
        }))

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
