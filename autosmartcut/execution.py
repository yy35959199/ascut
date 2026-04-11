# Layer 3：清单加载、媒体时长、smartcut 成片；时间轴合并逻辑见 timeline_segments
from __future__ import annotations

import json
import logging
from fractions import Fraction
from pathlib import Path
from typing import Any

from autosmartcut.annotation_tokens import video_path_from_manifest
from autosmartcut.config import AppConfig, load_config
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
):
    """
    从句级 annotations 与 keep_mask 生成 positive_segments。
    返回 (segments, video_path, duration)。
    """
    from smartcut.media_container import MediaContainer

    media = MediaContainer(str(video))
    try:
        duration = float(media.duration)
    finally:
        media.close()

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
                video, duration, cfg.execution
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


def run_execution_layer(
    run: PipelineRun,
    *,
    config: AppConfig | None = None,
    pre_pad: float = 0.15,
    post_pad: float = 0.25,
    min_duration: float = 1.0,
    gap_after_cap: float | None = None,
    vad_snap_disabled_by_cli: bool = False,
) -> Path:
    """L3 端到端：清单 ``annotations`` + ``current.keep_mask`` → 输出视频。"""
    from autosmartcut.log import ensure_autosmartcut_logging
    from smartcut.media_container import MediaContainer
    from smartcut.misc_data import AudioExportInfo, AudioExportSettings
    from smartcut.smart_cut import smart_cut

    ensure_autosmartcut_logging(verbose=False)

    if gap_after_cap is None:
        gap_after_cap = (
            config.execution.gap_after_cap if config is not None else load_config().execution.gap_after_cap
        )

    mp = run.manifest_path
    data = load_manifest(mp)
    annotations = data.get("annotations")
    if not isinstance(annotations, list):
        raise ValueError("清单缺少 annotations[]")
    cur = data.get("current")
    if not isinstance(cur, dict):
        raise ValueError("清单缺少 current")
    keep_mask = cur.get("keep_mask")
    if not isinstance(keep_mask, list):
        raise ValueError("清单缺少 current.keep_mask[]")

    video = video_path_from_manifest(data, mp)

    logger.info("[L3] 开始执行层")
    positive, video_resolved, duration = positive_segments_from_annotations(
        annotations,
        keep_mask,
        video,
        pre_pad=pre_pad,
        post_pad=post_pad,
        min_duration=min_duration,
        gap_after_cap=gap_after_cap,
        config=config,
        vad_snap_disabled_by_cli=vad_snap_disabled_by_cli,
    )
    if not positive:
        raise ValueError("keep_mask 解析后无保留区间")

    out = run.output_video
    try:
        if out.resolve() == video_resolved.resolve():
            raise ValueError(
                "输出视频路径与源视频解析为同一路径，已中止以免覆盖原始文件；"
                "请使用默认 ascut_out_* 目录或指定与源文件不同的 --output-dir / --output-name。"
            )
    except OSError:
        pass

    out.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "[L3] 源视频 %s 时长 %.2fs keep 段数 %d → %s",
        video_resolved,
        duration,
        len(positive),
        out,
    )

    media = MediaContainer(str(video_resolved))
    try:
        if not media.audio_tracks:
            raise ValueError("输入文件没有音轨")
        audio_info = AudioExportInfo(
            output_tracks=[
                AudioExportSettings(codec="passthru") for _ in media.audio_tracks
            ]
        )
        err = smart_cut(
            media_container=media,
            positive_segments=positive,
            out_path=str(out),
            audio_export_info=audio_info,
        )
        if err is not None:
            raise RuntimeError(f"smart_cut 失败: {err}")
    finally:
        media.close()

    touch_layer_status(data, "l3")
    save_manifest(mp, data, atomic=True)

    logger.info("[L3] 完成 → %s", out)
    return out
