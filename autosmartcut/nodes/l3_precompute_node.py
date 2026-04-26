"""L3PrecomputeNode — 句级缓存预计算节点。

薄包装 l3_precompute.build_sentence_tile_cache()。
将返回的 sidecar_dir 路径写入 manifest["sentence_tile_cache"]。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from autosmartcut.pipeline_events import ProgressEvent
from autosmartcut.pipeline_models import L3PrecomputeOutput, StageResult, StageStatus

if TYPE_CHECKING:
    from autosmartcut.config import AppConfig
    from autosmartcut.pipeline_models import StageContext

logger = logging.getLogger(__name__)


class L3PrecomputeNode:
    """L3 预计算：VAD 吸附 + 全时间轴分片缓存。"""

    id = "l3_precompute"
    reads = frozenset({"annotations", "source_media"})
    writes = frozenset({"sentence_tile_cache"})
    phase = 1
    resumable = True

    def __init__(self, config: "AppConfig") -> None:
        self._config = config

    async def run(self, ctx: "StageContext") -> StageResult:
        """薄包装 l3_precompute.build_sentence_tile_cache()。"""
        from autosmartcut.l3_precompute import build_sentence_tile_cache

        manifest = ctx.manifest
        params = ctx.params
        manifest_path_str = params.get("manifest_path", "")

        if not manifest_path_str:
            return StageResult(
                status=StageStatus.FAILED,
                summary="manifest_path 未注入到 _params",
                error=RuntimeError("manifest_path 未注入到 _params"),
            )

        manifest_path = Path(manifest_path_str)
        run_id = str(manifest.get("run_id", ""))
        annotations = manifest.get("annotations", [])

        if not annotations:
            return StageResult(
                status=StageStatus.FAILED,
                summary="annotations 为空，无法预计算句级缓存",
                error=ValueError("annotations 为空"),
            )

        ctx.emit(ProgressEvent(node_id=self.id, phase="precompute_start", payload={
            "sentence_count": len(annotations),
        }))

        try:
            sidecar_dir, snapped_annotations = await asyncio.to_thread(
                build_sentence_tile_cache,
                run_id=run_id,
                manifest_path=manifest_path,
                manifest_data=manifest,
                annotations_l1b=annotations,
                config=self._config,
            )
        except Exception as e:
            logger.exception("[L3PrecomputeNode] build_sentence_tile_cache 失败: %s", e)
            return StageResult(
                status=StageStatus.FAILED,
                summary=f"L3 预计算失败: {e}",
                error=e,
            )

        # 将 sidecar_dir 路径写入 manifest
        manifest["sentence_tile_cache"] = str(sidecar_dir)

        # 读取 seam_index 获取 clip 数量
        try:
            from autosmartcut.l3_sidecar import load_seam_index
            index_obj = load_seam_index(sidecar_dir) or {}
            clip_count = len(index_obj.get("clips", []))
        except Exception:
            clip_count = 0

        ctx.emit(ProgressEvent(node_id=self.id, phase="precompute_done", payload={
            "elapsed_sec": 0.0,
            "clip_count": clip_count,
            "sidecar_dir": str(sidecar_dir),
        }))

        return StageResult(
            status=StageStatus.SUCCESS,
            summary=f"clip_count={clip_count} sidecar_dir={sidecar_dir}",
        )

    def summarize(self, manifest: dict) -> L3PrecomputeOutput:
        sidecar_dir_str = manifest.get("sentence_tile_cache", "")
        clip_count = 0
        if sidecar_dir_str:
            try:
                from autosmartcut.l3_sidecar import load_seam_index
                index_obj = load_seam_index(Path(sidecar_dir_str)) or {}
                clip_count = len(index_obj.get("clips", []))
            except Exception:
                pass
        return L3PrecomputeOutput(
            clip_count=clip_count,
            sidecar_dir=sidecar_dir_str,
        )
