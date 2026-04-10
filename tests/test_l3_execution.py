"""Layer 3（执行层）单测：区间与 gap 等纯逻辑，不依赖 LLM。"""

from autosmartcut.timeline_segments import collect_kept_intervals


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
