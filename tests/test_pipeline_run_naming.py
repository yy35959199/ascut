"""默认任务目录与日志文件时间串命名（run_id 仍为 ULID）。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from autosmartcut.manifest.manifest_io import make_manifest_skeleton, save_manifest
from autosmartcut.pipeline.pipeline_run import (
    PipelineRun,
    allocate_unique_dir,
    allocate_unique_file,
    format_label_ts,
)


def test_format_label_ts_three_digit_ms() -> None:
    dt = datetime(2026, 4, 27, 3, 4, 5, 123456)
    assert format_label_ts(dt) == "2026-04-27_03-04-05.123"


def test_allocate_unique_dir_suffix(tmp_path: Path) -> None:
    (tmp_path / "ascut_out_x").mkdir()
    p = allocate_unique_dir(tmp_path, "ascut_out_x")
    assert p.name == "ascut_out_x_01"


def test_allocate_unique_file_suffix(tmp_path: Path) -> None:
    (tmp_path / "run_x.log").write_text("x", encoding="utf-8")
    p = allocate_unique_file(tmp_path, "run_x", ".log")
    assert p.name == "run_x_01.log"


def test_new_default_dir_and_log_share_timestamp(tmp_path: Path) -> None:
    vp = tmp_path / "clip.mp4"
    vp.write_bytes(b"")
    run = PipelineRun.new(video_path=vp)
    assert re.fullmatch(
        r"ascut_out_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.\d{3}",
        run.output_dir.name,
    )
    assert re.fullmatch(
        r"run_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.\d{3}\.log",
        run.log_path.name,
    )
    ts_dir = run.output_dir.name.removeprefix("ascut_out_")
    ts_log = run.log_path.stem.removeprefix("run_")
    assert ts_dir == ts_log
    assert len(run.run_id) == 26


def test_new_output_dir_conflict_uses_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autosmartcut import pipeline_run as pr

    fixed = datetime(2026, 1, 2, 12, 0, 0, 0)

    class _DT:
        @staticmethod
        def now() -> datetime:
            return fixed

    monkeypatch.setattr(pr, "datetime", _DT)
    label = format_label_ts(fixed)
    (tmp_path / f"ascut_out_{label}").mkdir()

    vp = tmp_path / "v.mp4"
    vp.write_bytes(b"")
    run = PipelineRun.new(video_path=vp)
    assert run.output_dir.name == f"ascut_out_{label}_01"


def test_from_manifest_preserves_run_id(tmp_path: Path) -> None:
    vp = tmp_path / "src.mp4"
    vp.write_bytes(b"")
    mp = tmp_path / "timeline_manifest.json"
    sk = make_manifest_skeleton("MANIFEST_RID_12345", "goal", str(vp))
    save_manifest(mp, sk)
    run = PipelineRun.from_manifest(mp)
    assert run.run_id == "MANIFEST_RID_12345"
    assert run.log_path.parent == run.output_dir == tmp_path.resolve()


def test_fork_replaces_run_id(tmp_path: Path) -> None:
    vp = tmp_path / "src.mp4"
    vp.write_bytes(b"")
    mp = tmp_path / "timeline_manifest.json"
    sk = make_manifest_skeleton("OLD_RID", "g", str(vp))
    save_manifest(mp, sk)
    fork_parent = tmp_path / "fork_root"
    fork_parent.mkdir()
    run = PipelineRun.fork(mp, fork_parent)
    assert run.run_id != "OLD_RID"
    assert (fork_parent / "timeline_manifest.json").is_file()
    data = json.loads(
        (fork_parent / "timeline_manifest.json").read_text(encoding="utf-8")
    )
    assert data["run_id"] == run.run_id
