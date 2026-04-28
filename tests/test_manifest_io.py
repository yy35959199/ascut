"""manifest_io 单测。"""

from pathlib import Path

import pytest

from autosmartcut.manifest.manifest_io import (
    MANIFEST_FILENAME,
    MANIFEST_VERSION,
    load_manifest,
    ls_clear_node,
    ls_get_run_status,
    ls_mark_completed,
    ls_mark_failed,
    ls_mark_started,
    make_manifest_skeleton,
    save_manifest,
    strip_volatile_fields,
    touch_layer_status,
    validate_manifest_l1_text_prereq,
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


def test_strip_volatile_removes_legacy_l1_keys() -> None:
    d: dict = {
        "l1a_chunks": [],
        "l1_contract": {},
        "annotations_l1a": [{"index": 0}],
        "current": {},
    }
    strip_volatile_fields(d)
    assert "l1a_chunks" not in d
    assert "l1_contract" not in d
    assert "annotations_l1a" not in d


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


def test_validate_manifest_l1_text_prereq_ok(tmp_path: Path) -> None:
    p = tmp_path / MANIFEST_FILENAME
    d = make_manifest_skeleton("r", "", "v.mp4")
    d["raw_text"] = "你好。世界。"
    d["annotations"] = [
        {"index": 0, "content": "你好", "t_start": None, "t_end": None},
        {"index": 1, "content": "世界", "t_start": None, "t_end": None},
    ]
    save_manifest(p, d, atomic=True)
    validate_manifest_l1_text_prereq(p)


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


# ─────────────────────────────────────────────────────────────────────────────
# 新的三态模型测试
# ─────────────────────────────────────────────────────────────────────────────

def test_ls_mark_started() -> None:
    """测试标记节点开始执行。"""
    d = make_manifest_skeleton("r", "", "v.mp4")
    ls_mark_started(d, "l2a_comprehension")
    assert "l2a_comprehension" in d["layer_status"]
    assert "started_at" in d["layer_status"]["l2a_comprehension"]
    assert "completed_at" not in d["layer_status"]["l2a_comprehension"]


def test_ls_mark_completed() -> None:
    """测试标记节点完成执行。"""
    d = make_manifest_skeleton("r", "", "v.mp4")
    ls_mark_started(d, "l2a_comprehension")
    ls_mark_completed(d, "l2a_comprehension")
    assert "started_at" in d["layer_status"]["l2a_comprehension"]
    assert "completed_at" in d["layer_status"]["l2a_comprehension"]


def test_ls_mark_failed() -> None:
    """测试标记节点执行失败。"""
    d = make_manifest_skeleton("r", "", "v.mp4")
    ls_mark_started(d, "l2a_comprehension")
    ls_mark_failed(d, "l2a_comprehension")
    assert "started_at" in d["layer_status"]["l2a_comprehension"]
    assert "failed_at" in d["layer_status"]["l2a_comprehension"]
    assert "completed_at" not in d["layer_status"]["l2a_comprehension"]


def test_ls_clear_node() -> None:
    """测试清除节点状态记录。"""
    d = make_manifest_skeleton("r", "", "v.mp4")
    ls_mark_started(d, "l2a_comprehension")
    ls_mark_completed(d, "l2a_comprehension")
    ls_clear_node(d, "l2a_comprehension")
    assert "l2a_comprehension" not in d["layer_status"]


def test_ls_get_run_status_never_started() -> None:
    """测试获取未开始的节点状态。"""
    d = make_manifest_skeleton("r", "", "v.mp4")
    status = ls_get_run_status(d, "l2a_comprehension")
    assert status == "never_started"


def test_ls_get_run_status_started() -> None:
    """测试获取已开始但未完成的节点状态（中断）。"""
    d = make_manifest_skeleton("r", "", "v.mp4")
    ls_mark_started(d, "l2a_comprehension")
    status = ls_get_run_status(d, "l2a_comprehension")
    assert status == "started"


def test_ls_get_run_status_completed() -> None:
    """测试获取已完成的节点状态。"""
    d = make_manifest_skeleton("r", "", "v.mp4")
    ls_mark_started(d, "l2a_comprehension")
    ls_mark_completed(d, "l2a_comprehension")
    status = ls_get_run_status(d, "l2a_comprehension")
    assert status == "completed"


def test_ls_get_run_status_failed() -> None:
    """测试获取已失败的节点状态。"""
    d = make_manifest_skeleton("r", "", "v.mp4")
    ls_mark_started(d, "l2a_comprehension")
    ls_mark_failed(d, "l2a_comprehension")
    status = ls_get_run_status(d, "l2a_comprehension")
    assert status == "failed"


def test_ls_get_run_status_backward_compat_completed_only() -> None:
    """测试向后兼容：仅有 completed_at（来自旧格式迁移）也视为完成。"""
    d = make_manifest_skeleton("r", "", "v.mp4")
    # 模拟旧格式迁移后的状态：仅有 completed_at，无 started_at
    d["layer_status"]["l2a_comprehension"] = {"completed_at": "2026-01-01T00:00:00"}
    status = ls_get_run_status(d, "l2a_comprehension")
    assert status == "completed"


def test_migrate_layer_status_old_format() -> None:
    """测试旧格式迁移到新格式。"""
    from autosmartcut.manifest.manifest_io import migrate_layer_status
    
    d = {
        "layer_status": {
            "l2a_comprehension_completed_at": "2026-01-01T00:00:00",
            "l2b_decision_started_at": "2026-01-01T00:00:01",
            "l2b_decision_failed_at": "2026-01-01T00:00:02",
        }
    }
    migrate_layer_status(d)
    
    # 验证迁移后的格式
    assert d["layer_status"]["l2a_comprehension"]["completed_at"] == "2026-01-01T00:00:00"
    assert d["layer_status"]["l2b_decision"]["started_at"] == "2026-01-01T00:00:01"
    assert d["layer_status"]["l2b_decision"]["failed_at"] == "2026-01-01T00:00:02"


def test_migrate_layer_status_idempotent() -> None:
    """测试迁移是幂等的（多次调用结果相同）。"""
    from autosmartcut.manifest.manifest_io import migrate_layer_status
    
    d = {
        "layer_status": {
            "l2a_comprehension_completed_at": "2026-01-01T00:00:00",
        }
    }
    migrate_layer_status(d)
    first_result = dict(d["layer_status"])
    
    migrate_layer_status(d)
    second_result = dict(d["layer_status"])
    
    assert first_result == second_result
