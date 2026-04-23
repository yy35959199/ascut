from pathlib import Path

import pytest

from autosmartcut.l3_errors import L3InputError
from autosmartcut.l3_planner import build_plan


def test_build_plan_rejects_same_input_output_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    src.write_text("x", encoding="utf-8")
    manifest_data = {
        "source_media": {"path": str(src)},
        "annotations": [{"index": 0, "t_start": 0.0, "t_end": 1.0, "gap_after": 0.0}],
        "current": {"keep_mask": [{"index": 0, "keep": True}]},
    }
    manifest_path = tmp_path / "timeline_manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr("autosmartcut.l3_planner.video_path_from_manifest", lambda _data, _mp: src)
    monkeypatch.setattr("autosmartcut.l3_planner.probe_duration_seconds", lambda _p: 10.0)

    with pytest.raises(L3InputError):
        build_plan(
            run_id="r1",
            manifest_data=manifest_data,
            manifest_path=manifest_path,
            output_video=src,
            config=None,
            pre_pad=0.0,
            post_pad=0.0,
            min_duration=0.1,
            gap_after_cap=0.0,
            vad_snap_disabled_by_cli=True,
        )

