# Layer 3：JSON 加载、媒体时长、smartcut 成片；时间轴合并逻辑见 timeline_segments
from __future__ import annotations

import json
import logging
from fractions import Fraction
from pathlib import Path
from typing import Any

from autosmartcut.config import AppConfig, load_config
from autosmartcut.pipeline_run import PipelineRun
# 向后兼容：原从 execution 导入的合并辅助函数仍可用；实现均在 timeline_segments
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


def positive_segments_from_mask_files(
    layer1_path: Path,
    mask_path: Path,
    *,
    pre_pad: float = 0.15,
    post_pad: float = 0.25,
    min_duration: float = 1.0,
    gap_after_cap: float | None = None,
):
    """
    从 layer1_annotations.json 与 layer2 输出 JSON 生成 positive_segments。
    返回 (segments, video_path, duration)。
    """
    from smartcut.media_container import MediaContainer

    layer1 = load_json(layer1_path)
    mask_doc = load_json(mask_path)
    annotations = layer1["annotations"]
    keep_mask = mask_doc["keep_mask"]
    video = resolve_media_path(layer1["source"], layer1_path)

    media = MediaContainer(str(video))
    try:
        duration = float(media.duration)
    finally:
        media.close()

    if gap_after_cap is None:
        gap_after_cap = load_config().execution.gap_after_cap

    positive: list[tuple[Fraction, Fraction]] = keep_mask_to_positive_segments(
        annotations,
        keep_mask,
        video_duration=duration,
        pre_pad=pre_pad,
        post_pad=post_pad,
        min_duration=min_duration,
        gap_after_cap=gap_after_cap,
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
) -> Path:
    """L3 端到端：JSON1 + JSON3 → 输出视频，返回输出路径。"""
    from autosmartcut.log import ensure_autosmartcut_logging
    from smartcut.media_container import MediaContainer
    from smartcut.misc_data import AudioExportInfo, AudioExportSettings
    from smartcut.smart_cut import smart_cut

    ensure_autosmartcut_logging(verbose=False)

    if gap_after_cap is None:
        gap_after_cap = (
            config.execution.gap_after_cap if config is not None else load_config().execution.gap_after_cap
        )

    logger.info("[L3] 开始执行层")
    positive, video, duration = positive_segments_from_mask_files(
        run.json1_path,
        run.json3_path,
        pre_pad=pre_pad,
        post_pad=post_pad,
        min_duration=min_duration,
        gap_after_cap=gap_after_cap,
    )
    if not positive:
        raise ValueError("keep_mask 解析后无保留区间")

    out = run.output_video
    out.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "[L3] 源视频 %s 时长 %.2fs keep 段数 %d → %s",
        video,
        duration,
        len(positive),
        out,
    )

    media = MediaContainer(str(video))
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

    logger.info("[L3] 完成 → %s", out)
    return out
