from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from autosmartcut.config import AppConfig, load_config
from autosmartcut.l3_assembler import assemble_from_tile_clips, assemble_output
from autosmartcut.l3_encoder import encode_misses_sync
from autosmartcut.l3_models import L3RunMetrics
from autosmartcut.l3_planner import build_plan, validate_l3_manifest
from autosmartcut.l3_resolver import build_tasks, resolve_tasks
from autosmartcut.l3_sidecar import (
    load_assets_index,
    load_seam_index,
    persist_resolved_index,
    sidecar_dir_for_manifest,
    write_metrics,
    write_plan,
    write_tasks,
)
from autosmartcut.l3_tile_paths import collect_tile_clip_paths_for_keep

logger = logging.getLogger(__name__)


def run_l3_orchestration(
    *,
    run_id: str,
    manifest_data: dict[str, Any],
    manifest_path: Path,
    output_video: Path,
    config: AppConfig | None,
    pre_pad: float,
    post_pad: float,
    min_duration: float,
    gap_after_cap: float | None,
    vad_snap_disabled_by_cli: bool,
) -> tuple[Path, int]:
    t0 = time.perf_counter()
    sidecar_dir = sidecar_dir_for_manifest(manifest_path, run_id)

    annotations, keep_mask = validate_l3_manifest(manifest_data)
    cfg = config if config is not None else load_config()
    cache_on = getattr(cfg.execution, "sentence_tile_cache_enabled", True)
    seam = load_seam_index(sidecar_dir) if cache_on else None
    clip_paths: list[Path] | None = None
    if seam:
        clip_paths = collect_tile_clip_paths_for_keep(
            seam, annotations, keep_mask, sidecar_dir
        )

    if clip_paths:
        try:
            t_plan_0 = time.perf_counter()
            plan, positive, _, _ = build_plan(
                run_id=run_id,
                manifest_data=manifest_data,
                manifest_path=manifest_path,
                output_video=output_video,
                config=cfg,
                pre_pad=pre_pad,
                post_pad=post_pad,
                min_duration=min_duration,
                gap_after_cap=gap_after_cap,
                vad_snap_disabled_by_cli=vad_snap_disabled_by_cli,
                tile_cache_fast_planning=True,
            )
            write_plan(sidecar_dir, plan)
            t_assemble_0 = time.perf_counter()
            out = assemble_from_tile_clips(
                output_video=output_video, clip_paths=clip_paths
            )
            assemble_ms = int((time.perf_counter() - t_assemble_0) * 1000)
            total_ms = int((time.perf_counter() - t0) * 1000)
            plan_ms = int((t_assemble_0 - t_plan_0) * 1000)
            metrics = L3RunMetrics(
                resolve_assets_ms=plan_ms,
                encode_miss_ms=0,
                assemble_mux_ms=assemble_ms,
                total_l3_ms=total_ms,
                task_total=0,
                task_hit=0,
                task_miss=0,
                fallback_count=0,
                status="success",
                output_path=str(out),
                sentence_tile_fast_path=True,
            )
            write_metrics(sidecar_dir, run_id, metrics)
            logger.info("[L3] 句级分片快速路径成功 segments=%d", len(positive))
            return out, len(positive)
        except Exception as exc:
            logger.warning(
                "[L3] 句级分片快速路径失败，回退 smartcut: %s",
                exc,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )

    plan, positive, _, _ = build_plan(
        run_id=run_id,
        manifest_data=manifest_data,
        manifest_path=manifest_path,
        output_video=output_video,
        config=cfg,
        pre_pad=pre_pad,
        post_pad=post_pad,
        min_duration=min_duration,
        gap_after_cap=gap_after_cap,
        vad_snap_disabled_by_cli=vad_snap_disabled_by_cli,
        tile_cache_fast_planning=False,
    )
    write_plan(sidecar_dir, plan)

    t_resolve_0 = time.perf_counter()
    tasks = build_tasks(plan)
    write_tasks(sidecar_dir, run_id, tasks)
    assets_index = load_assets_index(sidecar_dir, run_id)
    resolved = resolve_tasks(tasks, assets_index)
    resolve_ms = int((time.perf_counter() - t_resolve_0) * 1000)
    persist_resolved_index(sidecar_dir, run_id, resolved)

    t_encode_0 = time.perf_counter()
    encode_result = encode_misses_sync(resolved)
    encode_ms = int((time.perf_counter() - t_encode_0) * 1000)

    t_assemble_0 = time.perf_counter()
    out = assemble_output(plan=plan, positive_segments=positive, resolved_tasks=resolved)
    assemble_ms = int((time.perf_counter() - t_assemble_0) * 1000)

    total_ms = int((time.perf_counter() - t0) * 1000)
    hit_count = sum(1 for item in resolved if item.hit)
    miss_count = len(resolved) - hit_count
    metrics = L3RunMetrics(
        resolve_assets_ms=resolve_ms,
        encode_miss_ms=encode_ms,
        assemble_mux_ms=assemble_ms,
        total_l3_ms=total_ms,
        task_total=len(resolved),
        task_hit=hit_count,
        task_miss=miss_count,
        fallback_count=encode_result.fallback_count,
        status="success",
        output_path=str(out),
        sentence_tile_fast_path=False,
    )
    write_metrics(sidecar_dir, run_id, metrics)
    return out, len(positive)
