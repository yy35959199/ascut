"""由 keep_mask 与 seam_index 计算需拼接的分片路径。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _merged_kept_intervals_seconds(
    annotations: list[dict[str, Any]],
    keep_mask: list[dict[str, Any]],
) -> list[tuple[float, float]]:
    keep: dict[int, bool] = {}
    for row in keep_mask:
        keep[int(row["index"])] = row.get("keep") is True
    anns = sorted(
        [a for a in annotations if isinstance(a, dict) and a.get("t_start") is not None],
        key=lambda a: int(a.get("index", 0)),
    )
    intervals: list[tuple[float, float]] = []
    i = 0
    while i < len(anns):
        idx = int(anns[i].get("index", i))
        if not keep.get(idx):
            i += 1
            continue
        t0 = float(anns[i]["t_start"])
        j = i + 1
        while j < len(anns) and keep.get(int(anns[j].get("index", j))):
            j += 1
        last = j - 1
        t1 = float(anns[last]["t_end"])
        intervals.append((t0, t1))
        i = j

    if not intervals:
        return []
    intervals.sort(key=lambda x: x[0])
    merged: list[tuple[float, float]] = []
    cs, ce = intervals[0]
    for a, b in intervals[1:]:
        if a <= ce:
            ce = max(ce, b)
        else:
            merged.append((cs, ce))
            cs, ce = a, b
    merged.append((cs, ce))
    return merged


def collect_tile_clip_paths_for_keep(
    seam_index: dict[str, Any],
    annotations: list[dict[str, Any]],
    keep_mask: list[dict[str, Any]],
    sidecar_dir: Path,
) -> list[Path] | None:
    """若所有需用分片文件存在且与目标时间并相交，返回按播放顺序的路径列表；否则 ``None``。"""
    clips = sorted(
        seam_index.get("clips", []),
        key=lambda c: int(c.get("clip_index", 0)),
    )
    if not clips:
        return None

    targets = _merged_kept_intervals_seconds(annotations, keep_mask)
    if not targets:
        return None

    def intersects_any(s: float, e: float) -> bool:
        if e <= s:
            return False
        for a, b in targets:
            if not (e <= a or s >= b):
                return True
        return False

    paths: list[Path] = []
    for c in clips:
        if str(c.get("state", "")).lower() != "ready":
            return None
        rel = c.get("clip_path")
        if not isinstance(rel, str):
            return None
        p = (sidecar_dir / rel).resolve()
        if not p.is_file():
            return None
        s = float(c.get("start_sec", 0.0))
        e = float(c.get("end_sec", 0.0))
        if intersects_any(s, e):
            paths.append(p)
    if not paths:
        return None
    return paths
