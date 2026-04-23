from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from autosmartcut.l3_errors import L3CacheError
from autosmartcut.l3_models import L3FinalPlan, L3RunMetrics, ResolvedTask, SeamTask


def sidecar_dir_for_manifest(manifest_path: Path, run_id: str) -> Path:
    return manifest_path.parent / ".ascut_sidecar" / run_id


def write_json(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        raise L3CacheError(f"写入 sidecar 失败: {path}") from exc


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise L3CacheError(f"读取 sidecar 失败: {path}") from exc


def canonical_signature(payload: dict[str, Any]) -> str:
    norm = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def write_plan(sidecar_dir: Path, plan: L3FinalPlan) -> Path:
    p = sidecar_dir / "l3_plan.json"
    write_json(p, plan.to_json())
    return p


def write_tasks(sidecar_dir: Path, run_id: str, tasks: list[SeamTask]) -> Path:
    p = sidecar_dir / "l3_tasks.json"
    write_json(
        p,
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "tasks": [t.to_json() for t in tasks],
        },
    )
    return p


def load_assets_index(sidecar_dir: Path, run_id: str) -> dict[str, Any]:
    p = sidecar_dir / "l3_assets_index.json"
    if not p.exists():
        return {"schema_version": "1.0", "run_id": run_id, "assets": []}
    return read_json(p)


def find_asset_by_cache_key(index: dict[str, Any], cache_key: str) -> dict[str, Any] | None:
    for item in index.get("assets", []):
        if item.get("cache_key") == cache_key:
            return item
    return None


def persist_resolved_index(sidecar_dir: Path, run_id: str, resolved: list[ResolvedTask]) -> Path:
    p = sidecar_dir / "l3_assets_index.json"
    assets = []
    for r in resolved:
        assets.append(
            {
                "cache_key": r.task.cache_key,
                "task_id": r.task.task_id,
                "state": r.asset_state or ("ready" if r.hit else "stale"),
                "asset_path": str(r.asset_path) if r.asset_path else None,
                "hit": r.hit,
                "miss_reason": r.miss_reason,
            }
        )
    write_json(
        p,
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "assets": assets,
        },
    )
    return p


def write_metrics(sidecar_dir: Path, run_id: str, metrics: L3RunMetrics) -> Path:
    p = sidecar_dir / "l3_metrics.json"
    write_json(p, {"run_id": run_id, **metrics.to_json()})
    return p


def write_seam_index(sidecar_dir: Path, data: dict[str, Any]) -> Path:
    """写入句级分片缓存索引（``seam_index.json``）。"""
    p = sidecar_dir / "seam_index.json"
    write_json(p, data)
    return p


def load_seam_index(sidecar_dir: Path) -> dict[str, Any] | None:
    p = sidecar_dir / "seam_index.json"
    if not p.is_file():
        return None
    return read_json(p)

