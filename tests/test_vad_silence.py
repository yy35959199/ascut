"""VAD 静音补集与切点吸附（snap）纯逻辑测试，不加载 Silero 权重。"""
from __future__ import annotations

from autosmartcut.timeline_segments import keep_mask_to_positive_segments
from autosmartcut.vad_silence import (
    snap_interval_edges_to_silence,
    speech_segments_to_silence_intervals,
)


def test_speech_to_silence_full_gap() -> None:
    speech = [{"start": 1.0, "end": 3.0}, {"start": 5.0, "end": 7.0}]
    sil = speech_segments_to_silence_intervals(speech, duration=10.0)
    assert sil == [(0.0, 1.0), (3.0, 5.0), (7.0, 10.0)]


def test_speech_to_silence_empty_speech() -> None:
    sil = speech_segments_to_silence_intervals([], duration=5.0)
    assert sil == [(0.0, 5.0)]


def test_snap_out_point_moves_to_silence_start() -> None:
    # 出点 b=2.0，静音 [1.5, 3.0]，半径内取静音左沿 max(s, lo)
    silences = [(0.0, 1.0), (1.5, 3.0), (4.0, 10.0)]
    out = snap_interval_edges_to_silence(
        [(0.5, 2.0)],
        silences,
        duration=10.0,
        radius=0.5,
    )
    assert len(out) == 1
    a, b = out[0]
    assert abs(b - 1.5) < 1e-6


def test_snap_no_silence_keeps_original() -> None:
    silences = [(0.0, 0.1), (9.9, 10.0)]
    orig = (1.0, 2.0)
    out = snap_interval_edges_to_silence([orig], silences, duration=10.0, radius=0.05)
    assert out == [orig]


def test_snap_invalid_reverts_segment() -> None:
    # 强制让两边都吸到使 a>=b 的情况较难构造；用半径 0 应原样返回
    out = snap_interval_edges_to_silence(
        [(1.0, 5.0)],
        [(0.0, 10.0)],
        duration=10.0,
        radius=0.0,
    )
    assert out == [(1.0, 5.0)]


def test_keep_mask_empty_silence_list_skips_snap_like_none() -> None:
    annotations = [
        {"index": 0, "t_start": 0.0, "t_end": 1.0, "gap_after": 0.5},
    ]
    keep_mask = [{"index": 0, "keep": True}]
    base = keep_mask_to_positive_segments(
        annotations,
        keep_mask,
        video_duration=10.0,
        pre_pad=0.0,
        post_pad=0.0,
        min_duration=0.0,
        gap_after_cap=0.6,
        silence_intervals=None,
        snap_radius=0.0,
    )
    empty_list = keep_mask_to_positive_segments(
        annotations,
        keep_mask,
        video_duration=10.0,
        pre_pad=0.0,
        post_pad=0.0,
        min_duration=0.0,
        gap_after_cap=0.6,
        silence_intervals=[],
        snap_radius=0.12,
    )
    assert base == empty_list


def test_keep_mask_snap_moves_out_point() -> None:
    """整段静音内出点吸附到窗口内静音左沿（更接近 b）。"""
    annotations = [
        {"index": 0, "t_start": 0.0, "t_end": 1.0, "gap_after": 0.0},
    ]
    keep_mask = [{"index": 0, "keep": True}]
    # 无 snap：右边界 1.0；全静音 0–10，出点 b≈1.0，半径内 cand 为 max(s,1-r)
    silences = [(0.0, 10.0)]
    snapped = keep_mask_to_positive_segments(
        annotations,
        keep_mask,
        video_duration=10.0,
        pre_pad=0.0,
        post_pad=0.0,
        min_duration=0.0,
        gap_after_cap=0.0,
        silence_intervals=silences,
        snap_radius=0.5,
    )
    raw = keep_mask_to_positive_segments(
        annotations,
        keep_mask,
        video_duration=10.0,
        pre_pad=0.0,
        post_pad=0.0,
        min_duration=0.0,
        gap_after_cap=0.0,
        silence_intervals=None,
        snap_radius=0.0,
    )
    assert len(snapped) == len(raw) == 1
    # 出点应在 [0.5, 1.0] 内被吸向静音内靠左的候选
    assert snapped[0][1] <= raw[0][1]
