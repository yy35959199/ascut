"""L1Node — 识别层（分块 ASR + 逐块强制对齐 + 单次分句与时间回填）。

薄包装 perception.run_l1_chunked()。
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autosmartcut.annotation_tokens import tokens_from_annotations
from autosmartcut.manifest_io import load_manifest, save_manifest
from autosmartcut.pipeline_events import ProgressEvent
from autosmartcut.pipeline_models import L1Output, StageResult, StageStatus
from autosmartcut.pipeline_run import PipelineRun
from autosmartcut.progress_utils import SpeedEstimator

if TYPE_CHECKING:
    from autosmartcut.config import AppConfig
    from autosmartcut.pipeline_models import StageContext

logger = logging.getLogger(__name__)


class L1Node:
    """L1：ASR + 强制对齐，产出带时间戳的 annotations。"""

    id = "l1_perception"
    reads = frozenset({"source_media"})
    writes = frozenset({"annotations", "raw_text"})
    phase = 1
    resumable = False

    def __init__(self, config: "AppConfig") -> None:
        self._config = config

    async def run(self, ctx: "StageContext") -> StageResult:
        from autosmartcut.perception import run_l1_chunked

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

        state: dict[str, Any] = {
            "done": False,
            "phase": "idle",
            "chunk_id": 0,
            "total_chunks": 0,
            "chunk_start_time": 0.0,
            "chunk_audio_sec": 0.0,
            "completed_audio_sec": 0.0,
            "total_audio_sec": 0.0,
            "last_text_preview": "",
        }
        estimator = SpeedEstimator()

        def on_progress(event: ProgressEvent) -> None:
            ctx.emit(event)
            p = event.payload
            phase = event.phase

            if phase == "plan_done":
                state["total_chunks"] = int(p.get("total_chunks", 0))
                state["total_audio_sec"] = float(p.get("total_audio_sec", 0.0))

            elif phase == "asr_chunk_start":
                chunk_id = int(p.get("chunk_id", 0))
                state["chunk_id"] = chunk_id
                state["chunk_audio_sec"] = float(p.get("chunk_audio_sec", 0.0))
                state["completed_audio_sec"] = float(p.get("completed_audio_sec", 0.0))
                state["chunk_start_time"] = time.monotonic()
                state["phase"] = "chunk_0" if chunk_id == 0 else "asr_running"

            elif phase == "asr_chunk_done":
                chunk_id = int(p.get("chunk_id", 0))
                audio_sec = float(p.get("chunk_audio_sec", 0.0))
                elapsed_sec = float(p.get("chunk_elapsed_sec", 0.0))
                estimator.record(chunk_id, audio_sec, elapsed_sec)
                state["completed_audio_sec"] = float(p.get("completed_audio_sec", 0.0))
                state["last_text_preview"] = str(p.get("text_preview", ""))
                state["phase"] = "idle"

        async def progress_ticker() -> None:
            while not state["done"]:
                await asyncio.sleep(1.0)
                if state["done"]:
                    break

                current_phase = state["phase"]

                if current_phase == "chunk_0":
                    ctx.emit(ProgressEvent(
                        node_id=self.id,
                        phase="asr_computing_speed",
                        payload={},
                    ))

                elif current_phase == "asr_running":
                    chunk_elapsed = time.monotonic() - state["chunk_start_time"]
                    frac = estimator.interpolate(state["chunk_audio_sec"], chunk_elapsed)
                    intra = frac * state["chunk_audio_sec"]
                    effective = state["completed_audio_sec"] + intra
                    total = state["total_audio_sec"]
                    pct = min(effective / total * 100, 99.0) if total > 0 else 0.0
                    remaining = estimator.estimate_remaining(total - effective)

                    ctx.emit(ProgressEvent(
                        node_id=self.id,
                        phase="asr_intra_chunk_progress",
                        payload={
                            "pct": pct,
                            "remaining_sec": remaining,
                            "chunk_id": state["chunk_id"],
                            "total_chunks": state["total_chunks"],
                            "last_text_preview": state["last_text_preview"],
                        },
                    ))

        ticker_task = asyncio.create_task(progress_ticker())
        try:
            await asyncio.to_thread(
                run_l1_chunked,
                run,
                asr_model_path=self._config.models.asr_model_path,
                forced_aligner_path=self._config.models.forced_aligner_path,
                config=self._config,
                backend=self._config.models.backend,
                progress_callback=on_progress,
            )
        except Exception as e:
            logger.exception("[L1Node] run_l1_chunked 失败: %s", e)
            return StageResult(
                status=StageStatus.FAILED,
                summary=f"L1 失败: {e}",
                error=e,
            )
        finally:
            state["done"] = True
            ticker_task.cancel()
            try:
                await ticker_task
            except asyncio.CancelledError:
                pass

        updated = load_manifest(manifest_path)
        manifest.update(updated)
        anns = manifest.get("annotations", [])
        try:
            manifest["tokens"] = tokens_from_annotations(anns)
        except Exception as e:
            logger.warning("[L1Node] tokens_from_annotations 失败: %s，跳过", e)

        try:
            save_manifest(manifest_path, manifest, atomic=True)
        except Exception as e:
            logger.warning("[L1Node] 保存 tokens 到磁盘失败: %s", e)

        ann_count = len(anns)
        raw_text_len = len(manifest.get("raw_text", ""))

        return StageResult(
            status=StageStatus.SUCCESS,
            summary=f"annotations={ann_count} raw_text_len={raw_text_len}",
        )

    def summarize(self, manifest: dict) -> L1Output:
        anns = manifest.get("annotations", [])
        aligned_count = sum(1 for a in anns if a.get("t_start") is not None)
        return L1Output(
            annotation_count=len(anns),
            aligned_count=aligned_count,
            raw_text_length=len(manifest.get("raw_text", "")),
            duration_seconds=float(
                manifest.get("source_media", {}).get("duration", 0.0)
            ),
        )
