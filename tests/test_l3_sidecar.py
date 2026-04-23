from pathlib import Path

from autosmartcut.l3_models import L3FinalPlan, L3RunMetrics, FinalSegment, SeamTask
from autosmartcut.l3_sidecar import (
    canonical_signature,
    find_asset_by_cache_key,
    load_assets_index,
    persist_resolved_index,
    sidecar_dir_for_manifest,
    write_metrics,
    write_plan,
    write_tasks,
)
from autosmartcut.l3_models import ResolvedTask
from fractions import Fraction


def test_sidecar_write_and_load(tmp_path: Path) -> None:
    manifest = tmp_path / "timeline_manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    sidecar = sidecar_dir_for_manifest(manifest, "rid1")

    plan = L3FinalPlan(
        run_id="rid1",
        source_video=tmp_path / "in.mp4",
        output_video=tmp_path / "out.mp4",
        segments=[FinalSegment(idx=0, start=Fraction(1, 1), end=Fraction(2, 1))],
        params_signature=canonical_signature({"a": 1}),
    )
    write_plan(sidecar, plan)
    write_tasks(
        sidecar,
        "rid1",
        [SeamTask(task_id="t1", segment_idx=0, boundary="in", target_cut=Fraction(1, 1), cache_key="k1")],
    )
    idx = load_assets_index(sidecar, "rid1")
    assert idx["assets"] == []

    resolved = [
        ResolvedTask(
            task=SeamTask(task_id="t1", segment_idx=0, boundary="in", target_cut=Fraction(1, 1), cache_key="k1"),
            hit=False,
            asset_state="stale",
            miss_reason="not_found",
        )
    ]
    persist_resolved_index(sidecar, "rid1", resolved)
    idx2 = load_assets_index(sidecar, "rid1")
    assert find_asset_by_cache_key(idx2, "k1") is not None

    metrics = L3RunMetrics(
        resolve_assets_ms=1,
        encode_miss_ms=2,
        assemble_mux_ms=3,
        total_l3_ms=6,
        task_total=1,
        task_hit=0,
        task_miss=1,
        fallback_count=1,
        status="success",
        output_path=str(tmp_path / "out.mp4"),
    )
    write_metrics(sidecar, "rid1", metrics)
    assert (sidecar / "l3_metrics.json").exists()

