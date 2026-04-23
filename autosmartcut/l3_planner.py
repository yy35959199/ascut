from __future__ import annotations

import logging
from fractions import Fraction
from pathlib import Path
from typing import Any

from autosmartcut.annotation_tokens import video_path_from_manifest
from autosmartcut.backends.smartcut_backend import probe_duration_seconds
from autosmartcut.config import AppConfig, load_config
from autosmartcut.l3_errors import L3InputError
from autosmartcut.l3_models import FinalSegment, L3FinalPlan
from autosmartcut.l3_sidecar import canonical_signature
from autosmartcut.timeline_segments import keep_mask_to_positive_segments

logger = logging.getLogger(__name__)


def resolve_audio_16k_path(manifest_data: dict[str, Any], manifest_path: Path) -> Path | None:
    sm = manifest_data.get("source_media")
    if not isinstance(sm, dict):
        return None
    rel = sm.get("audio_16k_path")
    if not isinstance(rel, str) or not rel.strip():
        return None
    cand = (manifest_path.parent / rel.strip()).resolve()
    return cand if cand.is_file() else None


def validate_l3_manifest(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    annotations = data.get("annotations")
    if not isinstance(annotations, list):
        raise L3InputError("清单缺少 annotations[]")
    for ann in annotations:
        if not isinstance(ann, dict):
            continue
        if ann.get("t_start") is None or ann.get("t_end") is None:
            raise L3InputError(
                "清单中 annotations 缺少 t_start/t_end，无法执行 L3；请先运行 ascut run --stage 1b 或完整 --stage 1"
            )
    cur = data.get("current")
    if not isinstance(cur, dict):
        raise L3InputError("清单缺少 current")
    keep_mask = cur.get("keep_mask")
    if not isinstance(keep_mask, list):
        raise L3InputError("清单缺少 current.keep_mask[]")
    return annotations, keep_mask


def build_plan(
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
    tile_cache_fast_planning: bool = False,
) -> tuple[L3FinalPlan, list[tuple[Fraction, Fraction]], Path, float]:
    annotations, keep_mask = validate_l3_manifest(manifest_data)
    video = video_path_from_manifest(manifest_data, manifest_path)
    duration = probe_duration_seconds(video)
    cfg = config if config is not None else load_config()
    local_gap_after_cap = cfg.execution.gap_after_cap if gap_after_cap is None else gap_after_cap

    silence_intervals: list[tuple[float, float]] | None = None
    snap_radius = 0.0
    audio_cache = resolve_audio_16k_path(manifest_data, manifest_path)
    if audio_cache is not None:
        logger.info("[L3] 复用 L1 缓存音轨 %s", audio_cache.name)
    eff_pre = 0.0 if tile_cache_fast_planning else pre_pad
    eff_post = 0.0 if tile_cache_fast_planning else post_pad
    eff_min_dur = 0.0 if tile_cache_fast_planning else min_duration
    if tile_cache_fast_planning:
        logger.info("[L3] tile_cache_fast_planning：跳过 VAD/snap，padding 置 0")
    elif not vad_snap_disabled_by_cli and cfg.execution.vad_snap_enabled and cfg.execution.vad_snap_radius > 0:
        try:
            from autosmartcut.vad_silence import silence_intervals_for_video

            silence_intervals = silence_intervals_for_video(
                video,
                duration,
                cfg.execution,
                audio_16k_path=audio_cache,
            )
            snap_radius = cfg.execution.vad_snap_radius
            logger.info("[L3] VAD 静音区间 %d 条，snap_radius=%.3fs", len(silence_intervals), snap_radius)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[L3] VAD 静音构建失败，跳过切点吸附: %s", exc)

    positive = keep_mask_to_positive_segments(
        annotations,
        keep_mask,
        video_duration=duration,
        pre_pad=eff_pre,
        post_pad=eff_post,
        min_duration=eff_min_dur,
        gap_after_cap=local_gap_after_cap,
        silence_intervals=None if tile_cache_fast_planning else silence_intervals,
        snap_radius=0.0 if tile_cache_fast_planning else snap_radius,
    )
    if not positive:
        raise L3InputError("keep_mask 解析后无保留区间")

    signature_payload = {
        "pre_pad": eff_pre,
        "post_pad": eff_post,
        "min_duration": eff_min_dur,
        "gap_after_cap": local_gap_after_cap,
        "vad_snap_disabled": vad_snap_disabled_by_cli,
        "tile_cache_fast_planning": tile_cache_fast_planning,
        "segment_count": len(positive),
    }
    segs = [FinalSegment(idx=i, start=s, end=e) for i, (s, e) in enumerate(positive)]
    plan = L3FinalPlan(
        run_id=run_id,
        source_video=video,
        output_video=output_video,
        segments=segs,
        params_signature=canonical_signature(signature_payload),
    )
    try:
        if output_video.resolve() == video.resolve():
            raise L3InputError(
                "输出视频路径与源视频解析为同一路径，已中止以免覆盖原始文件；"
                "请使用默认 ascut_out_* 目录或指定与源文件不同的 --output-dir / --output-name。"
            )
    except OSError:
        pass
    return plan, positive, video, duration

