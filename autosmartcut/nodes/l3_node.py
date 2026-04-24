"""L3Node — 执行层节点。

薄包装 execution.run_execution_layer()。
将输出视频路径写入 manifest["output_video"]。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from autosmartcut.pipeline_events import ProgressEvent
from autosmartcut.pipeline_models import L3Output, StageResult, StageStatus
from autosmartcut.pipeline_run import PipelineRun

if TYPE_CHECKING:
    from autosmartcut.config import AppConfig
    from autosmartcut.pipeline_models import StageContext

logger = logging.getLogger(__name__)


class L3Node:
    """L3：执行层节点（清单 annotations + keep_mask → 输出视频）。"""

    id = "l3_execute"
    # l2d_completed 确保 l3_execute 在 l2d_human 完成后才调度
    reads = frozenset({"annotations", "keep_mask", "source_media", "sentence_tile_cache", "l2d_completed"})
    writes = frozenset({"output_video"})
    phase = 3
    resumable = True

    def __init__(self, config: "AppConfig") -> None:
        self._config = config

    async def run(self, ctx: "StageContext") -> StageResult:
        """薄包装 execution.run_execution_layer()。"""
        from autosmartcut.execution import run_execution_layer

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

        ctx.emit(ProgressEvent(node_id=self.id, message="L3 编排中（计算保留片段）..."))

        # 同步 keep_mask 到 manifest["current"]["keep_mask"]（execution.py 从此处读取）
        if "keep_mask" in manifest:
            manifest.setdefault("current", {})["keep_mask"] = manifest["keep_mask"]

        try:
            out_path = await asyncio.to_thread(
                run_execution_layer,
                run,
                config=self._config,
            )
        except Exception as e:
            logger.exception("[L3Node] run_execution_layer 失败: %s", e)
            return StageResult(
                status=StageStatus.FAILED,
                summary=f"L3 执行失败: {e}",
                error=e,
            )

        ctx.emit(ProgressEvent(node_id=self.id, message=f"视频合成完成 → {out_path}"))

        # 将输出视频路径写入 manifest
        manifest["output_video"] = str(out_path)

        return StageResult(
            status=StageStatus.SUCCESS,
            summary=f"output_video={out_path}",
        )

    def summarize(self, manifest: dict) -> L3Output:
        output_video = manifest.get("output_video", "")
        duration = float(manifest.get("source_media", {}).get("duration", 0.0))
        # 尝试从 sidecar 获取 segment_count
        segment_count = 0
        sidecar_dir_str = manifest.get("sentence_tile_cache", "")
        if sidecar_dir_str:
            try:
                from autosmartcut.l3_sidecar import load_seam_index
                index_obj = load_seam_index(Path(sidecar_dir_str)) or {}
                segment_count = len(index_obj.get("clips", []))
            except Exception:
                pass
        return L3Output(
            output_video=output_video,
            segment_count=segment_count,
            duration_seconds=duration,
        )
