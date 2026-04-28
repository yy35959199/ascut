"""双轨 partial 合并逻辑单元测试。"""

from __future__ import annotations

from autosmartcut.nodes.l2.dual_track_merge import merge_partials_into_manifest


def test_merge_partials_order_l1b_then_l2() -> None:
    base: dict = {
        "annotations": [{"index": 0, "text": "old"}],
        "current": {"keep_mask": [{"index": 0, "keep": True}]},
        "layer_status": {},
    }
    l1b = {
        "annotations": [
            {"index": 0, "text": "aligned", "t_start": 0.0, "t_end": 1.0},
        ],
        "layer_status": {"l1b_completed_at": "t1"},
    }
    l2 = {
        "current": {
            "keep_mask": [{"index": 0, "keep": False}],
            "comprehension": {"x": 1},
        },
        "goal": "g2",
        "layer_status": {"l2_completed_at": "t2"},
    }
    merge_partials_into_manifest(base, l1b, l2)
    assert base["annotations"][0]["t_start"] == 0.0
    assert base["current"]["keep_mask"][0]["keep"] is False
    assert base["goal"] == "g2"
    assert base["layer_status"]["l1b_completed_at"] == "t1"
    assert base["layer_status"]["l2_completed_at"] == "t2"
