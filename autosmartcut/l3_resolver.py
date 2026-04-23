from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from autosmartcut.l3_models import L3FinalPlan, ResolvedTask, SeamTask
from autosmartcut.l3_sidecar import find_asset_by_cache_key


def _task_key(plan_signature: str, segment_idx: int, boundary: str, target_cut: Fraction) -> str:
    return f"{plan_signature}:{segment_idx}:{boundary}:{float(target_cut):.6f}"


def build_tasks(plan: L3FinalPlan) -> list[SeamTask]:
    tasks: list[SeamTask] = []
    for seg in plan.segments:
        for boundary, cut in (("in", seg.start), ("out", seg.end)):
            cache_key = _task_key(plan.params_signature, seg.idx, boundary, cut)
            tasks.append(
                SeamTask(
                    task_id=f"t_{seg.idx:04d}_{boundary}",
                    segment_idx=seg.idx,
                    boundary=boundary,  # type: ignore[arg-type]
                    target_cut=cut,
                    cache_key=cache_key,
                )
            )
    return tasks


def resolve_tasks(tasks: list[SeamTask], assets_index: dict) -> list[ResolvedTask]:
    resolved: list[ResolvedTask] = []
    for task in tasks:
        entry = find_asset_by_cache_key(assets_index, task.cache_key)
        if entry is None:
            resolved.append(ResolvedTask(task=task, hit=False, asset_state="stale", miss_reason="not_found"))
            continue
        state = str(entry.get("state") or "").lower()
        asset_path_raw = entry.get("asset_path")
        if state != "ready":
            resolved.append(ResolvedTask(task=task, hit=False, asset_state="stale", miss_reason="signature_mismatch"))
            continue
        if not isinstance(asset_path_raw, str) or not asset_path_raw.strip():
            resolved.append(ResolvedTask(task=task, hit=False, asset_state="invalid", miss_reason="asset_invalid"))
            continue
        asset_path = Path(asset_path_raw)
        if not asset_path.exists():
            resolved.append(ResolvedTask(task=task, hit=False, asset_state="invalid", miss_reason="asset_missing"))
            continue
        resolved.append(ResolvedTask(task=task, hit=True, asset_path=asset_path, asset_state="ready"))
    return resolved

