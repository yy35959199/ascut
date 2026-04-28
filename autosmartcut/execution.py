# Layer 3：清单加载、媒体时长、smartcut 成片；时间轴合并逻辑见 timeline_segments
from __future__ import annotations

import json
import logging
from fractions import Fraction
from pathlib import Path
from typing import Any

from autosmartcut.annotation_tokens import video_path_from_manifest
from autosmartcut.backends.smartcut_backend import (
    probe_duration_seconds as probe_duration_seconds_by_smartcut,
    render_segments,
)
from autosmartcut.config import AppConfig, load_config
from autosmartcut.l3_errors import L3InputError
from autosmartcut.log import log_stage, log_stage_result, setup_logging_for_manifest
from autosmartcut.manifest_io import load_manifest, save_manifest, touch_layer_status
from autosmartcut.pipeline_run import PipelineRun
from autosmartcut.timeline_segments import (  # noqa: F401
    apply_padding,
    collect_kept_intervals,
    intervals_to_fraction_segments,
    keep_mask_to_positive_segments,
    merge_short_intervals,
    resolve_keep_flags,
)

logger = logging.getLogger(__name__)


def probe_video_duration_seconds(video_path: Path) -> float:
    """
    仅从容器头读取时长，避免为取 duration 而做一次完整 MediaContainer demux。

    若容器无 ``duration`` 元数据（如部分 TS），回退到 ``MediaContainer`` 全量扫描，
    与 smartcut 原逻辑一致。
    """
    import av
    from av import time_base as AV_TIME_BASE

    with av.open(str(video_path), "r", metadata_errors="ignore") as container:
        if container.duration is not None:
            return float(Fraction(container.duration, AV_TIME_BASE))

    logger.info(
        "[L3] 容器头无 duration，回退 MediaContainer 扫描以获取时长: %s",
        video_path.name,
    )
    return probe_duration_seconds_by_smartcut(video_path)


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def resolve_media_path(source: str, ref_json: Path) -> Path:
    """解析 layer1 中的 source 字段为可读视频路径。"""
    p = Path(source)
    if p.is_file():
        return p.resolve()
    cand = ref_json.parent / source
    if cand.is_file():
        return cand.resolve()
    cand = Path.cwd() / source
    if cand.is_file():
        return cand.resolve()
    raise FileNotFoundError(f"找不到源视频: {source}（相对 {ref_json.parent} 或当前工作目录）")


def resolved_audio_16k_path(
    manifest_data: dict[str, Any], manifest_path: Path
) -> Path | None:
    """若 ``source_media.audio_16k_path`` 存在且可读，返回绝对路径；否则 ``None``。"""
    sm = manifest_data.get("source_media")
    if not isinstance(sm, dict):
        return None
    rel = sm.get("audio_16k_path")
    if not isinstance(rel, str) or not rel.strip():
        return None
    cand = (manifest_path.parent / rel.strip()).resolve()
    return cand if cand.is_file() else None


def positive_segments_from_annotations(
    annotations: list[dict[str, Any]],
    keep_mask: list[dict[str, Any]],
    video: Path,
    *,
    pre_pad: float = 0.15,
    post_pad: float = 0.25,
    min_duration: float = 1.0,
    gap_after_cap: float | None = None,
    config: AppConfig | None = None,
    vad_snap_disabled_by_cli: bool = False,
    audio_16k_path: Path | None = None,
):
    """
    从句级 annotations 与 keep_mask 生成 positive_segments。
    返回 (segments, video_path, duration)。
    """
    duration = probe_video_duration_seconds(video)

    cfg = config if config is not None else load_config()
    if gap_after_cap is None:
        gap_after_cap = cfg.execution.gap_after_cap

    silence_intervals: list[tuple[float, float]] | None = None
    snap_radius = 0.0
    if (
        not vad_snap_disabled_by_cli
        and cfg.execution.vad_snap_enabled
        and cfg.execution.vad_snap_radius > 0
    ):
        try:
            from autosmartcut.vad_silence import silence_intervals_for_video

            silence_intervals = silence_intervals_for_video(
                video,
                duration,
                cfg.execution,
                audio_16k_path=audio_16k_path,
            )
            snap_radius = cfg.execution.vad_snap_radius
            logger.info(
                "[L3] VAD 静音区间 %d 条，snap_radius=%.3fs",
                len(silence_intervals),
                snap_radius,
            )
        except Exception as exc:
            logger.warning("[L3] VAD 静音构建失败，跳过切点吸附: %s", exc)
            silence_intervals = None
            snap_radius = 0.0

    positive: list[tuple[Fraction, Fraction]] = keep_mask_to_positive_segments(
        annotations,
        keep_mask,
        video_duration=duration,
        pre_pad=pre_pad,
        post_pad=post_pad,
        min_duration=min_duration,
        gap_after_cap=gap_after_cap,
        silence_intervals=silence_intervals,
        snap_radius=snap_radius,
    )
    return positive, video, duration


def _validate_l3_manifest_for_execution(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """校验 L3 所需清单字段（原 l3_planner.validate_l3_manifest 逻辑）。"""
    annotations = data.get("annotations")
    if not isinstance(annotations, list):
        raise L3InputError("清单缺少 annotations[]")
    for ann in annotations:
        if not isinstance(ann, dict):
            continue
        if ann.get("t_start") is None or ann.get("t_end") is None:
            raise L3InputError(
                "清单中 annotations 缺少 t_start/t_end，无法执行 L3；请先运行 ascut run --stage 1（完整识别）"
            )
    cur = data.get("current")
    if not isinstance(cur, dict):
        raise L3InputError("清单缺少 current")
    keep_mask = cur.get("keep_mask")
    if not isinstance(keep_mask, list):
        raise L3InputError("清单缺少 current.keep_mask[]")
    return annotations, keep_mask


def run_execution_layer(
    run: PipelineRun,
    *,
    config: AppConfig | None = None,
    pre_pad: float = 0.15,
    post_pad: float = 0.25,
    min_duration: float = 1.0,
    gap_after_cap: float | None = None,
    vad_snap_disabled_by_cli: bool = False,
) -> tuple[Path, int]:
    """L3 端到端：清单 ``annotations`` + ``current.keep_mask`` → 输出视频。返回 (输出路径, 保留段数量)。"""
    setup_logging_for_manifest(run.manifest_path, verbose=False)

    if gap_after_cap is None:
        gap_after_cap = (
            config.execution.gap_after_cap if config is not None else load_config().execution.gap_after_cap
        )

    mp = run.manifest_path
    data = load_manifest(mp)
    out = run.output_video

    annotations, keep_mask = _validate_l3_manifest_for_execution(data)
    video = video_path_from_manifest(data, mp)
    audio_16k_path = resolved_audio_16k_path(data, mp)

    with log_stage("l3.orchestrate", out_path=str(out)):
        positive, video_resolved, _duration = positive_segments_from_annotations(
            annotations,
            keep_mask,
            video,
            pre_pad=pre_pad,
            post_pad=post_pad,
            min_duration=min_duration,
            gap_after_cap=gap_after_cap,
            config=config,
            vad_snap_disabled_by_cli=vad_snap_disabled_by_cli,
            audio_16k_path=audio_16k_path,
        )
        if not positive:
            raise L3InputError("keep_mask 解析后无保留区间")
        try:
            if out.resolve() == video_resolved.resolve():
                raise L3InputError(
                    "输出视频路径与源视频解析为同一路径，已中止以免覆盖原始文件；"
                    "请使用默认 ascut_out_* 目录或指定与源文件不同的 --output-dir / --output-name。"
                )
        except OSError:
            pass

        render_segments(
            source_video=video_resolved,
            output_video=out,
            positive_segments=positive,
        )

    with log_stage("l3.persist_manifest", manifest=str(mp)):
        touch_layer_status(data, "l3")
        save_manifest(mp, data, atomic=True)

    segment_count = len(positive)
    log_stage_result("l3.output", summary=f"video={out} segments={segment_count}")
    logger.info("[L3] 完成 → %s", out)
    return out, segment_count
