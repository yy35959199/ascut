"""L1bNode — 强制对齐节点。

薄包装 perception.run_l1b_align_only()。
依赖 L1aNode 写出的 audio_16k.wav。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from autosmartcut.manifest_io import load_manifest
from autosmartcut.perception import AUDIO_16K_WAV_NAME
from autosmartcut.pipeline_events import ProgressEvent
from autosmartcut.pipeline_models import L1bOutput, StageResult, StageStatus
from autosmartcut.pipeline_run import PipelineRun

if TYPE_CHECKING:
    from autosmartcut.config import AppConfig
    from autosmartcut.pipeline_models import StageContext

logger = logging.getLogger(__name__)


class L1bNode:
    """L1B：强制对齐节点，回填 t_start/t_end/gap_after。"""

    id = "l1b_align"
    reads = frozenset({"annotations_l1a", "source_media"})
    writes = frozenset({"annotations"})
    phase = 1
    resumable = True

    def __init__(self, config: "AppConfig") -> None:
        self._config = config

    async def run(self, ctx: "StageContext") -> StageResult:
        """薄包装 perception.run_l1b_align_only()。"""
        from autosmartcut.perception import run_l1b_align_only

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

        # 验证 audio_16k.wav 已存在（由 L1aNode 写出）
        wav_path = output_dir / AUDIO_16K_WAV_NAME
        if not wav_path.is_file():
            return StageResult(
                status=StageStatus.FAILED,
                summary=f"L1B 需要 L1A 产出的缓存音轨: {wav_path}",
                error=FileNotFoundError(f"L1B 需要 L1A 产出的缓存音轨: {wav_path}"),
            )

        source_media = manifest.get("source_media", {})
        video_path_str = source_media.get("path", "")
        video_path = Path(video_path_str) if video_path_str else output_dir / "input.mp4"
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

        ctx.emit(ProgressEvent(node_id=self.id, message="对齐模型加载中..."))

        try:
            await asyncio.to_thread(
                run_l1b_align_only,
                run,
                forced_aligner_path=self._config.models.forced_aligner_path,
                config=self._config,
                backend=self._config.models.backend,
            )
        except Exception as e:
            logger.exception("[L1bNode] run_l1b_align_only 失败: %s", e)
            return StageResult(
                status=StageStatus.FAILED,
                summary=f"L1B 强制对齐失败: {e}",
                error=e,
            )

        ctx.emit(ProgressEvent(node_id=self.id, message="强制对齐完成，加载结果..."))

        # 重新加载 manifest，更新内存中的 annotations
        updated = load_manifest(manifest_path)
        manifest.update(updated)

        annotations = manifest.get("annotations", [])
        ann_count = len(annotations)
        aligned_count = sum(
            1 for a in annotations
            if a.get("t_start") is not None and a.get("t_end") is not None
        )

        ctx.emit(ProgressEvent(
            node_id=self.id,
            message=f"L1B 完成：{ann_count} 句，{aligned_count} 句已对齐时间轴",
        ))

        return StageResult(
            status=StageStatus.SUCCESS,
            summary=f"annotations={ann_count} aligned={aligned_count}",
        )

    def summarize(self, manifest: dict) -> L1bOutput:
        annotations = manifest.get("annotations", [])
        aligned_count = sum(
            1 for a in annotations
            if a.get("t_start") is not None and a.get("t_end") is not None
        )
        return L1bOutput(
            annotation_count=len(annotations),
            aligned_count=aligned_count,
        )
