# Layer 3：由 layer1 清单 + keep_mask 编译 smartcut 保留区间
from __future__ import annotations

import json
import logging
from fractions import Fraction
from pathlib import Path
from typing import Any

from autosmartcut.config import AppConfig, load_config
from autosmartcut.pipeline_run import PipelineRun

logger = logging.getLogger(__name__)


def _ensure_indices(annotations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """保证每条 annotation 带连续 index；缺省时按列表下标补齐。"""
    out: list[dict[str, Any]] = []
    for i, ann in enumerate(annotations):
        row = dict(ann)
        row.setdefault("index", i)
        out.append(row)
    out.sort(key=lambda a: int(a["index"]))
    return out


def _keep_map_from_mask(keep_mask: list[dict[str, Any]]) -> dict[int, bool | None]:
    """JSON3 约定每条 keep 为 bool；若输入损坏则 get 可能为 None，按不保留处理。"""
    m: dict[int, bool | None] = {}
    for item in keep_mask:
        idx = int(item["index"])
        m[idx] = item.get("keep")
    return m


def resolve_keep_flags(
    annotations: list[dict[str, Any]],
    keep_by_index: dict[int, bool | None],
) -> list[bool]:
    """按排序后的 annotations 顺序，``keep`` 为 True 的 index 视为保留。"""
    anns = _ensure_indices(annotations)
    return [keep_by_index.get(int(ann["index"])) is True for ann in anns]


def _tail_seconds_after_t_end(ann: dict[str, Any], gap_after_cap: float) -> float:
    """句末向后延伸的秒数：min(gap_after, gap_after_cap)；cap<=0 时不延伸。"""
    if gap_after_cap <= 0:
        return 0.0
    raw = float(ann.get("gap_after") or 0)
    if raw <= 0:
        return 0.0
    return min(raw, gap_after_cap)


def collect_kept_intervals(
    annotations: list[dict[str, Any]],
    resolved: list[bool],
    *,
    gap_after_cap: float = 0.6,
) -> list[tuple[float, float]]:
    """按 index 顺序将连续 resolved=True 的条合并为时间区间。

    每段右边界取 **该段最后一句** 的 ``t_end + min(gap_after, gap_after_cap)``，
    避免长静音（大 gap_after）被整段吃进成片；中间句之间仍由 ``t_start``/``t_end``
    自然覆盖句间间隔。
    """
    anns = _ensure_indices(annotations)
    if len(anns) != len(resolved):
        raise ValueError("annotations 与 resolved 长度不一致")

    intervals: list[tuple[float, float]] = []
    i = 0
    while i < len(anns):
        if not resolved[i]:
            i += 1
            continue
        t0 = float(anns[i]["t_start"])
        j = i + 1
        while j < len(anns) and resolved[j]:
            j += 1
        last = j - 1
        t_end_last = float(anns[last]["t_end"])
        tail = _tail_seconds_after_t_end(anns[last], gap_after_cap)
        t1 = t_end_last + tail
        intervals.append((t0, t1))
        i = j

    return intervals


def _merge_overlapping(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not intervals:
        return []
    s = sorted(intervals, key=lambda x: x[0])
    merged: list[tuple[float, float]] = []
    cur_s, cur_e = s[0]
    for a, b in s[1:]:
        if a <= cur_e:
            cur_e = max(cur_e, b)
        else:
            merged.append((cur_s, cur_e))
            cur_s, cur_e = a, b
    merged.append((cur_s, cur_e))
    return merged


def apply_padding(
    intervals: list[tuple[float, float]],
    *,
    duration: float,
    pre: float,
    post: float,
) -> list[tuple[float, float]]:
    if pre == 0 and post == 0:
        out = [(max(0.0, a), min(duration, b)) for a, b in intervals]
        return _merge_overlapping([(a, b) for a, b in out if b > a])
    padded: list[tuple[float, float]] = []
    for a, b in intervals:
        padded.append((max(0.0, a - pre), min(duration, b + post)))
    return _merge_overlapping([(a, b) for a, b in padded if b > a])


def merge_short_intervals(
    intervals: list[tuple[float, float]],
    min_duration: float,
) -> list[tuple[float, float]]:
    """短于 min_duration 的区间与相邻区间合并（向左优先）。"""
    if min_duration <= 0 or not intervals:
        return intervals
    merged = _merge_overlapping(intervals)
    changed = True
    while changed:
        changed = False
        new_list: list[tuple[float, float]] = []
        i = 0
        while i < len(merged):
            a, b = merged[i]
            if b - a >= min_duration:
                new_list.append((a, b))
                i += 1
                continue
            if new_list:
                pa, pb = new_list[-1]
                new_list[-1] = (pa, max(pb, b))
                changed = True
                i += 1
                continue
            if i + 1 < len(merged):
                na, nb = merged[i + 1]
                new_list.append((a, max(b, nb)))
                changed = True
                i += 2
                continue
            new_list.append((a, b))
            i += 1
        merged = _merge_overlapping(new_list)
    return merged


def intervals_to_fraction_segments(
    intervals: list[tuple[float, float]],
    *,
    denominator_limit: int = 1_000_000,
) -> list[tuple[Fraction, Fraction]]:
    return [
        (
            Fraction(t0).limit_denominator(denominator_limit),
            Fraction(t1).limit_denominator(denominator_limit),
        )
        for t0, t1 in intervals
        if t1 > t0
    ]


def keep_mask_to_positive_segments(
    annotations: list[dict[str, Any]],
    keep_mask: list[dict[str, Any]],
    *,
    video_duration: float,
    pre_pad: float = 0.15,
    post_pad: float = 0.25,
    min_duration: float = 1.0,
    gap_after_cap: float = 0.6,
) -> list[tuple[Fraction, Fraction]]:
    """
    layer1 annotations（index / t_start / t_end / gap_after 等）+ layer2 keep_mask ->
    smartcut positive_segments（Fraction 秒，相对文件起点）。

    gap_after_cap：每段末尾在 ``t_end`` 基础上最多再纳入 ``min(gap_after, cap)`` 秒静音尾；
    设为 0 则退化为仅用句末 ``t_end`` 作为段尾（旧行为）。
    """
    anns = _ensure_indices(annotations)
    keep_by = _keep_map_from_mask(keep_mask)
    for i in range(len(anns)):
        idx = int(anns[i]["index"])
        if idx not in keep_by:
            raise ValueError(f"keep_mask 缺少 index={idx}")

    resolved = resolve_keep_flags(anns, keep_by)
    intervals = collect_kept_intervals(anns, resolved, gap_after_cap=gap_after_cap)
    intervals = apply_padding(intervals, duration=video_duration, pre=pre_pad, post=post_pad)
    intervals = merge_short_intervals(intervals, min_duration)
    return intervals_to_fraction_segments(intervals)


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
) -> tuple[list[tuple[Fraction, Fraction]], Path, float]:
    """
    从 layer1_annotations.json 与 layer2_output_mock.json 生成 positive_segments。
    返回 (segments, video_path, duration)。
    """
    layer1 = load_json(layer1_path)
    mask_doc = load_json(mask_path)
    annotations = layer1["annotations"]
    keep_mask = mask_doc["keep_mask"]
    video = resolve_media_path(layer1["source"], layer1_path)

    from smartcut.media_container import MediaContainer

    media = MediaContainer(str(video))
    try:
        duration = float(media.duration)
    finally:
        media.close()

    if gap_after_cap is None:
        gap_after_cap = load_config().execution.gap_after_cap

    positive = keep_mask_to_positive_segments(
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
