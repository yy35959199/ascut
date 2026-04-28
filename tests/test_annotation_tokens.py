"""annotation_tokens 单测。"""

from pathlib import Path

import pytest

from autosmartcut.nodes.l2.annotation_tokens import (
    tokens_from_annotations,
    validate_annotations,
    validate_tokens,
    video_path_from_manifest,
)


def test_tokens_from_annotations() -> None:
    anns = [
        {"index": 0, "content": "A"},
        {"index": 1, "content": "B"},
    ]
    t = tokens_from_annotations(anns)
    assert t == [{"index": 0, "text": "A"}, {"index": 1, "text": "B"}]


def test_validate_tokens_empty() -> None:
    with pytest.raises(ValueError):
        validate_tokens([])


def test_validate_annotations() -> None:
    validate_annotations([{"index": 0, "content": "x"}])
    with pytest.raises(ValueError):
        validate_annotations([])


def test_video_path_from_manifest_source_media(tmp_path: Path) -> None:
    v = tmp_path / "clip.mp4"
    v.write_bytes(b"fake")
    m = tmp_path / "timeline_manifest.json"
    data = {"source_media": {"path": "clip.mp4"}}
    assert video_path_from_manifest(data, m) == v.resolve()


def test_video_path_from_manifest_top_level_source(tmp_path: Path) -> None:
    v = tmp_path / "x.mp4"
    v.write_bytes(b"fake")
    m = tmp_path / "sub" / "timeline_manifest.json"
    m.parent.mkdir(parents=True)
    m.write_text("{}", encoding="utf-8")
    data = {"source": str(v)}
    assert video_path_from_manifest(data, m) == v.resolve()


def test_video_path_from_manifest_missing() -> None:
    m = Path("nope/timeline_manifest.json")
    with pytest.raises(ValueError):
        video_path_from_manifest({}, m)
