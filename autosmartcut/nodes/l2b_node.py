"""L2bNode — 决策子阶段节点。

薄包装 intelligence_2b.run_2b_decision()。
直接操作 ctx.manifest（run_2b_decision 就地修改并返回 manifest_dict）。
从 ctx.manifest["_params"] 读取 review_round、two_b_mode、review_fixes、force_pass。
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from autosmartcut.annotation_tokens import tokens_from_annotations
from autosmartcut.pipeline_events import ProgressEvent
from autosmartcut.pipeline_models import L2bOutput, StageResult, StageStatus

if TYPE_CHECKING:
    from autosmartcut.config import AppConfig
    from autosmartcut.pipeline_models import StageContext

logger = logging.getLogger(__name__)


class L2bNode:
    """L2B：决策子阶段（keep_mask 生成）。"""

    id = "l2b_decision"
    reads = frozenset({"comprehension", "annotations_l1a"})
    writes = frozenset({"keep_mask"})
    phase = 2
    resumable = True

    def __init__(self, config: "AppConfig") -> None:
        self._config = config

    async def run(self, ctx: "StageContext") -> StageResult:
        """薄包装 intelligence_2b.run_2b_decision()。"""
        from autosmartcut.intelligence_2b import run_2b_decision

        manifest = ctx.manifest
        params = manifest.get("_params", {})

        review_round = int(params.get("review_round", 0))
        two_b_mode = str(params.get("two_b_mode", self._config.intelligence.two_b_mode))
        review_fixes = params.get("review_fixes", None)
        force_pass = bool(params.get("force_pass", False))

        annotations_l1a = manifest.get("annotations_l1a", [])
        comprehension = manifest.get("comprehension", {})

        if not annotations_l1a:
            return StageResult(
                status=StageStatus.FAILED,
                summary="annotations_l1a 为空，无法执行 L2B 决策",
                error=ValueError("annotations_l1a 为空"),
            )

        if not comprehension:
            return StageResult(
                status=StageStatus.FAILED,
                summary="comprehension 为空，无法执行 L2B 决策",
                error=ValueError("comprehension 为空"),
            )

        # 确保 tokens 存在（从 annotations_l1a 派生）
        if "tokens" not in manifest:
            try:
                manifest["tokens"] = tokens_from_annotations(annotations_l1a)
            except Exception as e:
                logger.warning("[L2bNode] tokens_from_annotations 失败: %s", e)
                manifest["tokens"] = [
                    {"index": int(ann.get("index", i)), "text": str(ann.get("content", ""))}
                    for i, ann in enumerate(annotations_l1a)
                ]

        tokens = manifest.get("tokens", [])

        # force_pass：强制通过，不调用 LLM，直接全部保留
        if force_pass:
            logger.info("[L2bNode] force_pass=True，跳过 LLM，全部保留")
            keep_mask = [{"index": int(t["index"]), "keep": True} for t in tokens]
            manifest["keep_mask"] = keep_mask
            keep_count = len(keep_mask)
            ctx.emit(ProgressEvent(
                node_id=self.id,
                message=f"L2B 强制通过（已达最大审核轮次）：{keep_count} 句全部保留",
            ))
            return StageResult(
                status=StageStatus.SUCCESS,
                summary=f"force_pass keep={keep_count} cut=0 total={keep_count} round={review_round}",
            )

        is_rerun = bool(review_fixes)
        ctx.emit(ProgressEvent(
            node_id=self.id,
            message=f"决策中（轮次 {review_round}，模式 {two_b_mode}）{'（2c 修正重跑）' if is_rerun else ''}...",
        ))

        try:
            # run_2b_decision 直接修改 manifest 并返回
            await asyncio.to_thread(
                run_2b_decision,
                manifest,
                mode=two_b_mode,
                review_fixes=review_fixes if review_fixes else None,
            )
        except Exception as e:
            logger.exception("[L2bNode] run_2b_decision 失败: %s", e)
            return StageResult(
                status=StageStatus.FAILED,
                summary=f"L2B 决策失败: {e}",
                error=e,
            )

        keep_mask = manifest.get("keep_mask", [])
        keep_count = sum(1 for e in keep_mask if e.get("keep") is True)
        cut_count = sum(1 for e in keep_mask if e.get("keep") is False)
        total = len(keep_mask)

        ctx.emit(ProgressEvent(
            node_id=self.id,
            message=f"L2B 完成：保留 {keep_count}，删除 {cut_count}，共 {total} 句",
        ))

        return StageResult(
            status=StageStatus.SUCCESS,
            summary=f"keep={keep_count} cut={cut_count} total={total} round={review_round}",
        )

    def summarize(self, manifest: dict) -> L2bOutput:
        keep_mask = manifest.get("keep_mask", [])
        keep_count = sum(1 for e in keep_mask if e.get("keep") is True)
        cut_count = sum(1 for e in keep_mask if e.get("keep") is False)
        params = manifest.get("_params", {})
        review_round = int(params.get("review_round", 0))
        return L2bOutput(
            keep_count=keep_count,
            cut_count=cut_count,
            total=len(keep_mask),
            review_round=review_round,
        )
