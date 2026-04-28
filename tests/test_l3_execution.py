"""Layer 3（执行层）单测：区间与 gap 等纯逻辑，不依赖 LLM。"""

from datetime import datetime
from fractions import Fraction
from pathlib import Path

import pytest

from autosmartcut.backends.smartcut_backend import SmartcutBackendError
from autosmartcut.nodes.l3.l3_errors import L3InputError
from autosmartcut.pipeline.pipeline_run import PipelineRun
from autosmartcut.nodes.l3.timeline_segments import collect_kept_intervals


def test_run_execution_layer_rejects_same_input_output_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """输出路径与源视频相同则抛 L3InputError（原 l3_planner 同路径保护）。"""
    from autosmartcut import execution as execution_mod

    src = tmp_path / "in.mp4"
    src.write_text("x", encoding="utf-8")
    manifest_data = {
        "source_media": {"path": str(src)},
        "annotations": [{"index": 0, "t_start": 0.0, "t_end": 1.0, "gap_after": 0.0}],
        "current": {"keep_mask": [{"index": 0, "keep": True}]},
    }
    mp = tmp_path / "timeline_manifest.json"
    mp.write_text("{}", encoding="utf-8")

    run = PipelineRun(
        run_id="r1",
        manifest_path=mp,
        output_dir=tmp_path,
        output_video=src,
        goal="",
        started_at=datetime.now(),
        video_path=src,
        log_path=tmp_path / "run_test.log",
    )

    monkeypatch.setattr(execution_mod, "load_manifest", lambda _p: manifest_data)
    monkeypatch.setattr(execution_mod, "video_path_from_manifest", lambda _d, _m: src)
    monkeypatch.setattr(
        execution_mod,
        "positive_segments_from_annotations",
        lambda *a, **k: ([(Fraction(0), Fraction(1))], src, 10.0),
    )

    with pytest.raises(L3InputError):
        execution_mod.run_execution_layer(
            run, gap_after_cap=0.0, vad_snap_disabled_by_cli=True
        )


def test_run_execution_layer_propagates_smartcut_backend_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """render_segments 抛 SmartcutBackendError 时应原样向上抛出（不与 L3 异常混绑）。"""
    from autosmartcut import execution as execution_mod

    src = tmp_path / "in.mp4"
    out = tmp_path / "out.mp4"
    src.write_text("x", encoding="utf-8")
    manifest_data = {
        "source_media": {"path": str(src)},
        "annotations": [{"index": 0, "t_start": 0.0, "t_end": 1.0, "gap_after": 0.0}],
        "current": {"keep_mask": [{"index": 0, "keep": True}]},
    }
    mp = tmp_path / "timeline_manifest.json"
    mp.write_text("{}", encoding="utf-8")

    run = PipelineRun(
        run_id="r1",
        manifest_path=mp,
        output_dir=tmp_path,
        output_video=out,
        goal="",
        started_at=datetime.now(),
        video_path=src,
        log_path=tmp_path / "run_test.log",
    )

    monkeypatch.setattr(execution_mod, "load_manifest", lambda _p: manifest_data)
    monkeypatch.setattr(execution_mod, "video_path_from_manifest", lambda _d, _m: src)
    monkeypatch.setattr(
        execution_mod,
        "positive_segments_from_annotations",
        lambda *a, **k: ([(Fraction(0), Fraction(1))], src, 10.0),
    )

    def _boom(**kwargs: object) -> None:
        raise SmartcutBackendError("boom")

    monkeypatch.setattr(execution_mod, "render_segments", _boom)

    with pytest.raises(SmartcutBackendError, match="boom"):
        execution_mod.run_execution_layer(
            run, gap_after_cap=0.0, vad_snap_disabled_by_cli=True
        )


def test_collect_kept_intervals_caps_trailing_gap() -> None:
    annotations = [
        {"index": 0, "t_start": 0.0, "t_end": 5.0, "gap_after": 100.0},
        {"index": 1, "t_start": 105.0, "t_end": 110.0, "gap_after": 2.0},
    ]
    resolved = [True, False]
    intervals = collect_kept_intervals(annotations, resolved, gap_after_cap=0.5)
    assert intervals == [(0.0, 5.5)]


def test_collect_kept_intervals_uses_small_gap_when_below_cap() -> None:
    annotations = [
        {"index": 0, "t_start": 0.0, "t_end": 1.0, "gap_after": 0.2},
    ]
    resolved = [True]
    intervals = collect_kept_intervals(annotations, resolved, gap_after_cap=0.8)
    assert intervals == [(0.0, 1.2)]


def test_collect_kept_intervals_merged_run_tail_on_last_sentence() -> None:
    annotations = [
        {"index": 0, "t_start": 0.0, "t_end": 5.0, "gap_after": 1.0},
        {"index": 1, "t_start": 6.0, "t_end": 10.0, "gap_after": 50.0},
    ]
    resolved = [True, True]
    intervals = collect_kept_intervals(annotations, resolved, gap_after_cap=0.8)
    assert intervals == [(0.0, 10.8)]


def test_collect_kept_intervals_cap_zero_no_tail() -> None:
    annotations = [
        {"index": 0, "t_start": 0.0, "t_end": 5.0, "gap_after": 100.0},
    ]
    resolved = [True]
    intervals = collect_kept_intervals(annotations, resolved, gap_after_cap=0.0)
    assert intervals == [(0.0, 5.0)]
