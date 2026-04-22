"""manifest_io 单测。"""

from pathlib import Path

import pytest

from autosmartcut.manifest_io import (
    MANIFEST_FILENAME,
    MANIFEST_VERSION,
    load_manifest,
    make_manifest_skeleton,
    save_manifest,
    strip_volatile_fields,
    touch_layer_status,
    validate_manifest_for_l1b,
    validate_manifest_for_stages,
    write_l2_checkpoint,
)


def test_make_manifest_skeleton() -> None:
    d = make_manifest_skeleton("rid", "g", "/v/a.mp4", duration=12.5)
    assert d["version"] == MANIFEST_VERSION
    assert d["run_id"] == "rid"
    assert d["goal"] == "g"
    assert d["source_media"]["path"] == "/v/a.mp4"
    assert d["source_media"]["duration"] == 12.5
    assert d["annotations"] == []
    assert d["current"] == {}


def test_save_load_roundtrip_atomic(tmp_path: Path) -> None:
    p = tmp_path / MANIFEST_FILENAME
    data = make_manifest_skeleton("u1", "", "x.mp4")
    data["annotations"] = [{"index": 0, "content": "hi"}]
    save_manifest(p, data, atomic=True)
    out = load_manifest(p)
    assert out["run_id"] == "u1"
    assert len(out["annotations"]) == 1


def test_strip_volatile_fields() -> None:
    d: dict = {
        "current": {
            "tokens": [{"index": 0, "text": "a"}],
            "cleaned_annotations": [],
            "l2_checkpoints": {"2a_r1": {"completed_at": "x", "k": 1}},
            "comprehension": {
                "purpose": "p",
                "cleaned_annotations": [{"annotation_index": 0, "cleaned_content": "x"}],
            },
            "keep_mask": [{"index": 0, "keep": True}],
        }
    }
    strip_volatile_fields(d)
    assert "tokens" not in d["current"]
    assert "cleaned_annotations" not in d["current"]
    assert "l2_checkpoints" not in d["current"]
    assert "cleaned_annotations" not in d["current"]["comprehension"]
    assert d["current"]["comprehension"]["purpose"] == "p"
    assert d["current"]["keep_mask"]


def test_write_l2_checkpoint_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / MANIFEST_FILENAME
    data = make_manifest_skeleton("r", "", "x.mp4")
    write_l2_checkpoint(data, p, "2a_r1", {"purpose_rough": "hi"})
    loaded = load_manifest(p)
    cp = loaded["current"]["l2_checkpoints"]["2a_r1"]
    assert cp["purpose_rough"] == "hi"
    assert "completed_at" in cp


def test_validate_manifest_for_stages_l2_empty_anns() -> None:
    d = make_manifest_skeleton("r", "", "v.mp4")
    with pytest.raises(ValueError, match="annotations"):
        validate_manifest_for_stages(frozenset({2}), d)


def test_validate_manifest_for_stages_l3_missing_times() -> None:
    d = make_manifest_skeleton("r", "", "v.mp4")
    d["annotations"] = [
        {"index": 0, "t_start": None, "t_end": None, "content": "a", "gap_after": None},
    ]
    d["current"] = {"keep_mask": [{"index": 0, "keep": True}]}
    with pytest.raises(ValueError, match="t_start"):
        validate_manifest_for_stages(frozenset({3}), d)


def test_validate_manifest_for_l1b_ok(tmp_path: Path) -> None:
    p = tmp_path / MANIFEST_FILENAME
    d = make_manifest_skeleton("r", "", "v.mp4")
    d["raw_text"] = "你好。世界。"
    d["annotations"] = [
        {"index": 0, "content": "你好", "t_start": None, "t_end": None},
        {"index": 1, "content": "世界", "t_start": None, "t_end": None},
    ]
    save_manifest(p, d, atomic=True)
    validate_manifest_for_l1b(p)


def test_validate_manifest_for_stages_l3_ok() -> None:
    d = make_manifest_skeleton("r", "", "v.mp4")
    d["annotations"] = [
        {"index": 0, "t_start": 0.0, "t_end": 1.0, "content": "a", "gap_after": 0.1},
    ]
    d["current"] = {"keep_mask": [{"index": 0, "keep": True}]}
    validate_manifest_for_stages(frozenset({3}), d)


def test_validate_manifest_for_stages_l3_length_mismatch() -> None:
    d = make_manifest_skeleton("r", "", "v.mp4")
    d["annotations"] = [
        {"index": 0, "t_start": 0.0, "t_end": 1.0, "content": "a", "gap_after": 0.1},
        {"index": 1, "t_start": 1.0, "t_end": 2.0, "content": "b", "gap_after": 0.1},
    ]
    d["current"] = {"keep_mask": [{"index": 0, "keep": True}]}
    with pytest.raises(ValueError, match="长度"):
        validate_manifest_for_stages(frozenset({3}), d)


def test_touch_layer_status(tmp_path: Path) -> None:
    d = make_manifest_skeleton("r", "", "v.mp4")
    touch_layer_status(d, "l1")
    assert "l1_completed_at" in d["layer_status"]


def test_load_manifest_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "missing.json")
