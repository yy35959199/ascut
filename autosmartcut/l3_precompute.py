"""轨 B：VAD + 句边界吸附 + 全时间轴分片缓存（segment_mode）。"""

from __future__ import annotations

import copy
import logging
from fractions import Fraction
from pathlib import Path
from typing import Any

from autosmartcut.annotation_tokens import video_path_from_manifest
from autosmartcut.backends.smartcut_backend import probe_duration_seconds
from autosmartcut.backends.smartcut_core.misc_data import AudioExportInfo, AudioExportSettings
from autosmartcut.backends.smartcut_core.smart_cut import smart_cut
from autosmartcut.backends.smartcut_core.media_utils import VideoExportMode, VideoExportQuality
from autosmartcut.backends.smartcut_core.video_cutter import VideoSettings
from autosmartcut.config import AppConfig, load_config
from autosmartcut.l3_planner import resolve_audio_16k_path
from autosmartcut.l3_sidecar import (
    canonical_signature,
    sidecar_dir_for_manifest,
    write_seam_index,
)
from autosmartcut.vad_silence import silence_intervals_for_video, snap_interval_edges_to_silence

logger = logging.getLogger(__name__)

_EPS = 1e-4


def snap_annotations_sentence_edges(
    annotations: list[dict[str, Any]],
    *,
    silence_intervals: list[tuple[float, float]] | None,
    duration: float,
    snap_radius: float,
) -> list[dict[str, Any]]:
    """对每条句子的起止时间在半径内吸附到静音边界（选 A：不额外 padding）。"""
    out = copy.deepcopy(annotations)
    if not silence_intervals or snap_radius <= 0:
        return out
    for ann in out:
        if ann.get("t_start") is None or ann.get("t_end") is None:
            continue
        ts = float(ann["t_start"])
        te = float(ann["t_end"])
        sn = snap_interval_edges_to_silence(
            [(ts, te)],
            silence_intervals,
            duration=duration,
            radius=snap_radius,
        )
        if sn:
            ann["t_start"], ann["t_end"] = float(sn[0][0]), float(sn[0][1])
    return out


def _build_tile_positive_segments(
    annotations: list[dict[str, Any]],
    duration: float,
) -> tuple[list[tuple[Fraction, Fraction]], list[dict[str, Any]]]:
    """构造覆盖 [0,duration) 的连续分片：可选片头、句 i、句间间隙、…、可选片尾。"""
    anns = sorted(
        [a for a in annotations if isinstance(a, dict) and a.get("t_start") is not None],
        key=lambda a: int(a.get("index", 0)),
    )
    if not anns:
        return [], []

    meta: list[dict[str, Any]] = []
    segs: list[tuple[Fraction, Fraction]] = []

    t0_first = float(anns[0]["t_start"])
    if t0_first > _EPS:
        segs.append((Fraction(0), Fraction(t0_first).limit_denominator(1_000_000)))
        meta.append({"kind": "head", "index": -1})

    for i, ann in enumerate(anns):
        ts = float(ann["t_start"])
        te = float(ann["t_end"])
        idx = int(ann.get("index", i))

        # 跳过零时长或负时长句子（L1B 对齐偶尔产生 t_start == t_end）
        # smart_cut 要求 start_time < end_time，否则触发 AssertionError
        if te <= ts + _EPS:
            logger.warning(
                "[L3Precompute] 跳过零时长句子 index=%d (t_start=%.4f, t_end=%.4f)",
                idx, ts, te,
            )
        else:
            segs.append(
                (
                    Fraction(ts).limit_denominator(1_000_000),
                    Fraction(te).limit_denominator(1_000_000),
                )
            )
            meta.append({"kind": "sentence", "index": idx})

        if i + 1 < len(anns):
            ts_next = float(anns[i + 1]["t_start"])
            if ts_next > te + _EPS:
                segs.append(
                    (
                        Fraction(te).limit_denominator(1_000_000),
                        Fraction(ts_next).limit_denominator(1_000_000),
                    )
                )
                meta.append({"kind": "gap", "after_sentence_index": idx})

    t_end_last = float(anns[-1]["t_end"])
    if duration > t_end_last + _EPS:
        segs.append(
            (
                Fraction(t_end_last).limit_denominator(1_000_000),
                Fraction(duration).limit_denominator(1_000_000),
            )
        )
        meta.append({"kind": "tail", "index": -2})

    return segs, meta


def build_sentence_tile_cache(
    *,
    run_id: str,
    manifest_path: Path,
    manifest_data: dict[str, Any],
    annotations_l1b: list[dict[str, Any]],
    config: AppConfig | None = None,
) -> tuple[Path, list[dict[str, Any]]]:
    """VAD 吸附 + 全片分片缓存；返回 ``(sidecar_dir, 写入主清单用的 annotations)``。"""
    cfg = config if config is not None else load_config()
    video = video_path_from_manifest(manifest_data, manifest_path)
    duration = float(probe_duration_seconds(video))
    audio_cache = resolve_audio_16k_path(manifest_data, manifest_path)

    silence_intervals: list[tuple[float, float]] | None = None
    snap_radius = 0.0
    if cfg.execution.vad_snap_enabled and cfg.execution.vad_snap_radius > 0:
        try:
            silence_intervals = silence_intervals_for_video(
                video,
                duration,
                cfg.execution,
                audio_16k_path=audio_cache,
            )
            snap_radius = cfg.execution.vad_snap_radius
        except Exception as exc:  # pragma: no cover
            logger.warning("[L3Pre] VAD 失败，跳过句边界吸附: %s", exc)

    snapped = snap_annotations_sentence_edges(
        annotations_l1b,
        silence_intervals=silence_intervals,
        duration=duration,
        snap_radius=snap_radius,
    )

    for i, ann in enumerate(snapped):
        if ann.get("t_start") is not None and ann.get("t_end") is not None:
            if i + 1 < len(snapped) and snapped[i + 1].get("t_start") is not None:
                nxt = float(snapped[i + 1]["t_start"])
                ann["gap_after"] = max(0.0, nxt - float(ann["t_end"]))

    segs, meta = _build_tile_positive_segments(snapped, duration)
    if not segs:
        raise ValueError("句级分片为空，无法预切缓存")

    sidecar = sidecar_dir_for_manifest(manifest_path, run_id)
    clip_dir = sidecar / "sentence_clips"
    clip_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(clip_dir / "tile_#.mp4")

    sig_payload = {
        "schema": "seam_index_v1",
        "source_path": str(video.resolve()),
        "source_stat": _file_stat_sig(video),
        "duration": round(duration, 6),
        "vad_threshold": cfg.execution.vad_threshold,
        "vad_min_silence_ms": cfg.execution.vad_min_silence_ms,
        "vad_speech_pad_ms": cfg.execution.vad_speech_pad_ms,
        "vad_snap_radius": snap_radius,
        "snap_enabled": cfg.execution.vad_snap_enabled,
        "segments": [(float(a), float(b)) for a, b in segs],
    }
    pipeline_sig = canonical_signature(sig_payload)

    from autosmartcut.backends.smartcut_core.media_container import MediaContainer

    media = MediaContainer(str(video))
    try:
        if not media.audio_tracks:
            raise RuntimeError("输入文件没有音轨")
        audio_info = AudioExportInfo(
            output_tracks=[
                AudioExportSettings(codec="passthru") for _ in media.audio_tracks
            ]
        )
        err = smart_cut(
            media_container=media,
            positive_segments=segs,
            out_path=pattern,
            audio_export_info=audio_info,
            video_settings=VideoSettings(VideoExportMode.SMARTCUT, VideoExportQuality.LOW),
            segment_mode=True,
        )
        if err is not None:
            raise RuntimeError(f"smart_cut 预切失败: {err}")
    finally:
        media.close()

    clips_out: list[dict[str, Any]] = []
    for i, (m, (a, b)) in enumerate(zip(meta, segs, strict=True)):
        pad = len(str(len(segs)))
        name = f"tile_{str(i + 1).zfill(pad)}.mp4"
        rel = f"sentence_clips/{name}"
        clips_out.append(
            {
                "clip_index": i,
                "kind": m.get("kind", "unknown"),
                "sentence_index": m.get("index"),
                "after_sentence_index": m.get("after_sentence_index"),
                "start_sec": float(a),
                "end_sec": float(b),
                "clip_path": rel,
                "cache_key": canonical_signature({**sig_payload, "clip_index": i}),
                "state": "ready",
            }
        )
        abs_p = clip_dir / name
        if not abs_p.is_file():
            raise FileNotFoundError(f"预切输出缺失: {abs_p}")

    index_obj = {
        "schema_version": "1.0",
        "run_id": run_id,
        "mode": "sentence_tile_cache",
        "pipeline_signature": pipeline_sig,
        "source_video": str(video.resolve()),
        "clips": clips_out,
    }
    write_seam_index(sidecar, index_obj)
    logger.info(
        "[L3Pre] 句级分片缓存完成 clips=%d sidecar=%s",
        len(clips_out),
        sidecar,
    )
    return sidecar, snapped


def _file_stat_sig(p: Path) -> dict[str, Any]:
    try:
        st = p.stat()
        return {"mtime": int(st.st_mtime_ns), "size": st.st_size}
    except OSError:
        return {"mtime": 0, "size": 0}
