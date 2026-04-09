# Layer 3：由 layer1 清单 + keep_mask 编译 smartcut 保留区间
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from typing import Any


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
    m: dict[int, bool | None] = {}
    for item in keep_mask:
        idx = int(item["index"])
        m[idx] = item.get("keep")
    return m


def resolve_keep_flags(
    annotations: list[dict[str, Any]],
    keep_by_index: dict[int, bool | None],
) -> list[bool]:
    """
    当前 schema 为 speech-only annotations：
    keep == True -> 保留；其余值视为不保留。
    若历史数据仍带 type=silence，则沿用旧逻辑兼容。
    """
    anns = _ensure_indices(annotations)
    n = len(anns)

    if all(str(ann.get("type", "speech")) != "silence" for ann in anns):
        return [keep_by_index.get(int(ann["index"])) is True for ann in anns]

    speech_kept: list[bool | None] = [None] * n
    for i, ann in enumerate(anns):
        if ann.get("type") != "speech":
            continue
        idx = int(ann["index"])
        raw = keep_by_index.get(idx)
        speech_kept[i] = raw is True

    resolved = [False] * n
    for i, ann in enumerate(anns):
        typ = ann.get("type", "")
        if typ == "speech":
            resolved[i] = bool(speech_kept[i])
        elif typ == "silence":
            left_ok = False
            for j in range(i - 1, -1, -1):
                if anns[j].get("type") == "speech":
                    left_ok = speech_kept[j] is True
                    break
            right_ok = False
            for j in range(i + 1, n):
                if anns[j].get("type") == "speech":
                    right_ok = speech_kept[j] is True
                    break
            resolved[i] = left_ok and right_ok
        else:
            idx = int(ann["index"])
            resolved[i] = keep_by_index.get(idx) is True

    return resolved


def collect_kept_intervals(
    annotations: list[dict[str, Any]],
    resolved: list[bool],
) -> list[tuple[float, float]]:
    """按 index 顺序将连续 resolved=True 的条合并为时间区间。"""
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
        t1 = float(anns[i]["t_end"])
        j = i + 1
        while j < len(anns) and resolved[j]:
            t1 = max(t1, float(anns[j]["t_end"]))
            j += 1
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
) -> list[tuple[Fraction, Fraction]]:
    """
    layer1 annotations（含 index/t_start/t_end/type）+ layer2 keep_mask ->
    smartcut positive_segments（Fraction 秒，相对文件起点）。
    """
    anns = _ensure_indices(annotations)
    keep_by = _keep_map_from_mask(keep_mask)
    for i in range(len(anns)):
        idx = int(anns[i]["index"])
        if idx not in keep_by:
            raise ValueError(f"keep_mask 缺少 index={idx}")

    resolved = resolve_keep_flags(anns, keep_by)
    intervals = collect_kept_intervals(anns, resolved)
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

    positive = keep_mask_to_positive_segments(
        annotations,
        keep_mask,
        video_duration=duration,
        pre_pad=pre_pad,
        post_pad=post_pad,
        min_duration=min_duration,
    )
    return positive, video, duration
