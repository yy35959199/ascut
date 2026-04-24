"""L1aNode — ASR 转录节点。

薄包装 perception.run_l1a_asr_only()。
完成后将 manifest["annotations"] 复制到 manifest["annotations_l1a"]，
并从 annotations_l1a 派生 manifest["tokens"]。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from autosmartcut.annotation_tokens import tokens_from_annotations
from autosmartcut.manifest_io import load_manifest, save_manifest
from autosmartcut.pipeline_events import ProgressEvent
from autosmartcut.pipeline_models import L1aOutput, StageResult, StageStatus
from autosmartcut.pipeline_run import PipelineRun

if TYPE_CHECKING:
    from autosmartcut.config import AppConfig
    from autosmartcut.pipeline_models import StageContext

logger = logging.getLogger(__name__)


class L1aNode:
    """L1A：ASR 文本定稿节点。"""

    id = "l1a_asr"
    reads = frozenset({"source_media"})
    writes = frozenset({"annotations_l1a", "raw_text"})
    phase = 1
    resumable = False  # ASR 结果不稳定，始终重跑

    def __init__(self, config: "AppConfig") -> None:
        self._config = config

    async def run(self, ctx: "StageContext") -> StageResult:
        """薄包装 perception.run_l1a_asr_only()。"""
        from autosmartcut.perception import run_l1a_asr_only

        manifest = ctx.manifest
        params = manifest.get("_params", {})
        manifest_path_str = params.get("manifest_path", "")

        if not manifest_path_str:
            return StageResult(
                status=StageStatus.FAILED,
                summary="manifest_path 未注入到 _params",
                error=RuntimeError("manifest_path 未注入到 _params"),
            )

        manifest_path = Path(manifest_path_str)
        output_dir = manifest_path.parent

        source_media = manifest.get("source_media", {})
        video_path_str = source_media.get("path", "")
        if not video_path_str:
            return StageResult(
                status=StageStatus.FAILED,
                summary="manifest.source_media.path 为空",
                error=ValueError("manifest.source_media.path 为空"),
            )

        video_path = Path(video_path_str)
        if not video_path.is_absolute():
            video_path = (output_dir / video_path).resolve()

        run_id = str(manifest.get("run_id", ""))
        goal = str(manifest.get("goal", ""))
        suffix = video_path.suffix or ".mp4"
        output_video = output_dir / f"{video_path.stem}_cut{suffix}"

        run = PipelineRun(
            run_id=run_id,
            manifest_path=manifest_path,
            output_dir=output_dir,
            output_video=output_video,
            goal=goal,
            started_at=datetime.now(),
            video_path=video_path,
        )

        ctx.emit(ProgressEvent(node_id=self.id, message="音频转码中（提取 16kHz mono WAV）..."))

        try:
            await asyncio.to_thread(
                run_l1a_asr_only,
                run,
                asr_model_path=self._config.models.asr_model_path,
                config=self._config,
                backend=self._config.models.backend,
            )
        except Exception as e:
            logger.exception("[L1aNode] run_l1a_asr_only 失败: %s", e)
            return StageResult(
                status=StageStatus.FAILED,
                summary=f"L1A ASR 失败: {e}",
                error=e,
            )

        ctx.emit(ProgressEvent(node_id=self.id, message="ASR 推理完成，加载结果..."))

        # 重新加载 manifest，将 annotations 复制到 annotations_l1a
        updated = load_manifest(manifest_path)
        manifest.update(updated)
        manifest["annotations_l1a"] = list(updated.get("annotations", []))

        # 从 annotations_l1a 派生 tokens（index + text）
        try:
            manifest["tokens"] = tokens_from_annotations(manifest["annotations_l1a"])
        except Exception as e:
            logger.warning("[L1aNode] tokens_from_annotations 失败: %s，跳过", e)

        # 将 annotations_l1a 和 tokens 写回磁盘
        try:
            save_manifest(manifest_path, manifest, atomic=True)
        except Exception as e:
            logger.warning("[L1aNode] 保存 annotations_l1a/tokens 到磁盘失败: %s", e)

        ann_count = len(manifest["annotations_l1a"])
        raw_text_len = len(manifest.get("raw_text", ""))

        ctx.emit(ProgressEvent(
            node_id=self.id,
            message=f"ASR 转录完成：{ann_count} 句，原文 {raw_text_len} 字符",
        ))

        return StageResult(
            status=StageStatus.SUCCESS,
            summary=f"annotations={ann_count} raw_text_len={raw_text_len}",
        )

    def summarize(self, manifest: dict) -> L1aOutput:
        anns = manifest.get("annotations_l1a", [])
        return L1aOutput(
            annotation_count=len(anns),
            raw_text_length=len(manifest.get("raw_text", "")),
            duration_seconds=float(
                manifest.get("source_media", {}).get("duration", 0.0)
            ),
        )
