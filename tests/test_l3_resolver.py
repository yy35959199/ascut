from fractions import Fraction
from pathlib import Path

from autosmartcut.l3_models import L3FinalPlan, FinalSegment
from autosmartcut.l3_resolver import build_tasks, resolve_tasks


def test_build_tasks_generates_in_out_per_segment() -> None:
    plan = L3FinalPlan(
        run_id="r1",
        source_video=Path("in.mp4"),
        output_video=Path("out.mp4"),
        segments=[FinalSegment(idx=0, start=Fraction(1, 1), end=Fraction(2, 1))],
        params_signature="sig1",
    )
    tasks = build_tasks(plan)
    assert len(tasks) == 2
    assert tasks[0].boundary == "in"
    assert tasks[1].boundary == "out"


def test_resolve_tasks_hit_and_miss() -> None:
    plan = L3FinalPlan(
        run_id="r1",
        source_video=Path("in.mp4"),
        output_video=Path("out.mp4"),
        segments=[FinalSegment(idx=0, start=Fraction(1, 1), end=Fraction(2, 1))],
        params_signature="sig1",
    )
    tasks = build_tasks(plan)
    ready_key = tasks[0].cache_key
    missing_key = tasks[1].cache_key
    index = {
        "schema_version": "1.0",
        "run_id": "r1",
        "assets": [
            {
                "cache_key": ready_key,
                "state": "ready",
                "asset_path": str(Path(__file__).resolve()),
            }
        ],
    }
    resolved = resolve_tasks(tasks, index)
    hit = next(r for r in resolved if r.task.cache_key == ready_key)
    miss = next(r for r in resolved if r.task.cache_key == missing_key)
    assert hit.hit is True
    assert hit.asset_state == "ready"
    assert miss.hit is False
    assert miss.miss_reason == "not_found"

