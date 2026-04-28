"""L2aNode — 理解子阶段节点。

薄包装 intelligence_2a.run_2a_comprehension()。
直接操作 ctx.manifest（run_2a_comprehension 就地修改并返回 manifest_dict）。
若 tokens 不存在，从 annotations 派生。
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from autosmartcut.nodes.l2.annotation_tokens import tokens_from_annotations
from autosmartcut.pipeline.pipeline_events import ProgressEvent
from autosmartcut.pipeline.pipeline_models import L2aOutput, StageResult, StageStatus

if TYPE_CHECKING:
    from autosmartcut.config import AppConfig
    from autosmartcut.pipeline.pipeline_models import StageContext

logger = logging.getLogger(__name__)


class L2aNode:
    """L2A：理解子阶段（两轮 LLM + 稀疏纠错 + 稠密回填）。"""

    id = "l2a_comprehension"
    reads = frozenset({"annotations", "goal"})
    writes = frozenset({"comprehension"})
    phase = 2
    resumable = True

    def __init__(self, config: "AppConfig") -> None:
        self._config = config

    async def run(self, ctx: "StageContext") -> StageResult:
        """薄包装 intelligence_2a.run_2a_comprehension()。"""
        from autosmartcut.nodes.l2.intelligence_2a import run_2a_comprehension

        manifest = ctx.manifest
        annotations = manifest.get("annotations", [])

        if not annotations:
            return StageResult(
                status=StageStatus.FAILED,
                summary="annotations 为空，无法执行 L2A 理解",
                error=ValueError("annotations 为空"),
            )

        # 确保 tokens 存在（从 annotations 派生）
        if "tokens" not in manifest:
            try:
                manifest["tokens"] = tokens_from_annotations(annotations)
            except Exception as e:
                logger.warning("[L2aNode] tokens_from_annotations 失败: %s", e)
                # 手动构建 tokens
                manifest["tokens"] = [
                    {"index": int(ann.get("index", i)), "text": str(ann.get("content", ""))}
                    for i, ann in enumerate(annotations)
                ]

        tokens = manifest.get("tokens", [])
        logger.info("[L2aNode] tokens 数量: %d", len(tokens))
        ctx.emit(ProgressEvent(node_id=self.id, phase="r1_start", payload={}))

        try:
            # run_2a_comprehension 直接修改 manifest_dict 并返回
            await asyncio.to_thread(
                run_2a_comprehension,
                manifest,
            )
        except Exception as e:
            logger.exception("[L2aNode] run_2a_comprehension 失败: %s", e)
            return StageResult(
                status=StageStatus.FAILED,
                summary=f"L2A 理解失败: {e}",
                error=e,
            )

        comprehension = manifest.get("comprehension", {})
        purpose = comprehension.get("purpose", "")
        block_count = len(comprehension.get("outline_blocks", []))
        cleaned = comprehension.get("cleaned_annotations", [])
        tokens_list = manifest.get("tokens", [])
        correction_count = sum(
            1 for i, clean in enumerate(cleaned)
            if i < len(tokens_list) and tokens_list[i].get("text", "") != clean.get("cleaned_content", "")
        )

        ctx.emit(ProgressEvent(node_id=self.id, phase="r2_done", payload={
            "elapsed_sec": 0.0,
            "purpose": purpose[:80],
            "block_count": block_count,
            "correction_count": correction_count,
        }))

        return StageResult(
            status=StageStatus.SUCCESS,
            summary=f"purpose={purpose[:60]} blocks={block_count} corrections={correction_count}",
        )

    def summarize(self, manifest: dict) -> L2aOutput:
        comprehension = manifest.get("comprehension", {})
        purpose = comprehension.get("purpose", "")
        block_count = len(comprehension.get("outline_blocks", []))
        cleaned = comprehension.get("cleaned_annotations", [])
        tokens_list = manifest.get("tokens", [])
        correction_count = sum(
            1 for i, clean in enumerate(cleaned)
            if i < len(tokens_list) and tokens_list[i].get("text", "") != clean.get("cleaned_content", "")
        )
        return L2aOutput(
            purpose=purpose[:80],
            block_count=block_count,
            correction_count=correction_count,
        )
