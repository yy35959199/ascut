"""时间轴 + keep_mask → 保留片段（纯函数，无 smartcut / 媒体 I/O）。

供 **L2 预览** 与 **L3 成片** 共用同一套合并规则：输入为 L1 句级时间轴
（至少含 index、t_start、t_end、gap_after）与 L2 的 keep_mask，
输出为秒级区间或 ``Fraction`` 段供 smartcut。

L2 仅 import 本模块即可，无需加载 ``execution``（避免间接依赖重媒体栈）。
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any

__all__ = [
    "apply_padding",
    "collect_kept_intervals",
    "intervals_to_fraction_segments",
    "keep_mask_to_positive_segments",
    "merge_short_intervals",
    "resolve_keep_flags",
]


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
    layer1 句级时间轴（index / t_start / t_end / gap_after 等）+ layer2 keep_mask ->
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
