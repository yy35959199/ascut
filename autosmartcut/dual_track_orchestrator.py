"""L1A 后双轨并行：L2（API）与 L1B+媒体预切并行，Barrier 后合并主清单。"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autosmartcut.config import AppConfig
from autosmartcut.dual_track_merge import (
    L1B_PARTIAL_NAME,
    L2_PARTIAL_NAME,
    atomic_write_json,
    merge_dir_for_run,
    merge_partials_into_manifest,
)
from autosmartcut.intelligence import compute_l2_layer_result
from autosmartcut.manifest_io import (
    load_manifest,
    save_manifest,
    touch_layer_status,
    validate_manifest_for_stages,
)
from autosmartcut.perception import compute_l1b_aligned_annotations
from autosmartcut.pipeline_run import PipelineRun

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _l2_worker(
    manifest_path: Path,
    goal: str,
    out_partial: Path,
    *,
    auto: bool,
    two_b_mode: str,
) -> None:
    data = load_manifest(manifest_path)
    result = compute_l2_layer_result(
        data,
        goal,
        auto=auto,
        two_b_mode=two_b_mode,
        on_phase_save=None,
        on_after_2b_round=None,
    )
    cur: dict[str, Any] = {
        "comprehension": result.get("comprehension", {}),
        "keep_mask": result.get("keep_mask", []),
    }
    if result.get("review_report"):
        cur["review_report"] = result["review_report"]
    if result.get("human_feedback_history"):
        cur["human_feedback_history"] = result["human_feedback_history"]
    partial = {
        "current": cur,
        "goal": result.get("goal", goal),
        "layer_status": {"l2_completed_at": _iso_now()},
    }
    atomic_write_json(out_partial, partial)


def _l1b_media_worker(
    run: PipelineRun,
    forced_aligner_path: Path,
    cfg: AppConfig,
    out_partial: Path,
    *,
    backend: str,
    device: str,
    dtype: str,
    language: str,
    gpu_memory_utilization: float,
) -> None:
    from autosmartcut.l3_precompute import build_sentence_tile_cache

    data = load_manifest(run.manifest_path)
    aligned = compute_l1b_aligned_annotations(
        run,
        data,
        forced_aligner_path=forced_aligner_path,
        config=cfg,
        backend=backend,
        device=device,
        dtype=dtype,
        language=language,
        gpu_memory_utilization=gpu_memory_utilization,
    )
    _sidecar, snapped_ann = build_sentence_tile_cache(
        run_id=run.run_id,
        manifest_path=run.manifest_path,
        manifest_data=data,
        annotations_l1b=aligned,
        config=cfg,
    )
    from autosmartcut.perception import compact_annotations

    partial = {
        "annotations": compact_annotations(snapped_ann),
        "layer_status": {
            "l1b_completed_at": _iso_now(),
            "l1_completed_at": _iso_now(),
        },
    }
    atomic_write_json(out_partial, partial)


def run_dual_track_after_l1a(
    run: PipelineRun,
    *,
    forced_aligner_path: Path,
    config: AppConfig,
    auto: bool,
    two_b_mode: str,
    backend: str,
    device: str,
    dtype: str,
    language: str,
    gpu_memory_utilization: float,
    post_merge_validate_stages: frozenset[int] | None = None,
) -> None:
    """在 L1A 已完成的前提下并行跑 L2 与 L1B+句级缓存，再合并写入主清单。"""
    mp = run.manifest_path
    mdir = merge_dir_for_run(run)
    mdir.mkdir(parents=True, exist_ok=True)
    p_l1b = mdir / L1B_PARTIAL_NAME
    p_l2 = mdir / L2_PARTIAL_NAME

    goal = run.goal or ""

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_l2 = ex.submit(
            _l2_worker,
            mp,
            goal,
            p_l2,
            auto=auto,
            two_b_mode=two_b_mode,
        )
        f_l1b = ex.submit(
            _l1b_media_worker,
            run,
            forced_aligner_path,
            config,
            p_l1b,
            backend=backend,
            device=device,
            dtype=dtype,
            language=language,
            gpu_memory_utilization=gpu_memory_utilization,
        )
        for fut in as_completed([f_l2, f_l1b]):
            fut.result()

    if not p_l1b.is_file() or not p_l2.is_file():
        raise RuntimeError("双轨 partial 文件缺失，合并中止")

    l1b_obj = json.loads(p_l1b.read_text(encoding="utf-8"))
    l2_obj = json.loads(p_l2.read_text(encoding="utf-8"))
    base = load_manifest(mp)
    merge_partials_into_manifest(base, l1b_obj, l2_obj)
    stages = post_merge_validate_stages or frozenset({2})
    validate_manifest_for_stages(stages, base)
    touch_layer_status(base, "l2")
    save_manifest(mp, base, atomic=True)
    logger.info("[DualTrack] 已合并 L1B+L2 → %s", mp)
