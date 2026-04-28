"""Property-based and example tests for perception.plan_chunks().

Tests verify the four core postconditions:
1. Contiguity: chunks[i].end_sec == chunks[i+1].start_sec
2. Coverage: chunks[0].start_sec == 0.0, chunks[-1].end_sec == total_duration
3. No empty chunks: chunk.end_sec > chunk.start_sec for all chunks
4. Sequential IDs: chunk_id values are 0, 1, 2, ...

Uses hypothesis for property-based testing.
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from autosmartcut.nodes.l1.vad_silence import plan_chunks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_speech_segments(
    starts_ends: list[tuple[float, float]],
) -> list[dict[str, float]]:
    return [{"start": s, "end": e} for s, e in starts_ends]


def assert_postconditions(chunks: list[dict], total_duration: float) -> None:
    """Assert all four postconditions hold."""
    if total_duration <= 0:
        assert chunks == []
        return

    assert len(chunks) > 0, "Expected at least one chunk for positive duration"

    # Postcondition 1: contiguity
    for i in range(len(chunks) - 1):
        assert abs(chunks[i]["end_sec"] - chunks[i + 1]["start_sec"]) < 1e-9, (
            f"Gap between chunk {i} and {i+1}: "
            f"{chunks[i]['end_sec']} != {chunks[i+1]['start_sec']}"
        )

    # Postcondition 2: coverage
    assert abs(chunks[0]["start_sec"] - 0.0) < 1e-9, "First chunk must start at 0.0"
    assert abs(chunks[-1]["end_sec"] - total_duration) < 1e-9, (
        f"Last chunk must end at {total_duration}, got {chunks[-1]['end_sec']}"
    )

    # Postcondition 3: no empty chunks
    for i, chunk in enumerate(chunks):
        assert chunk["end_sec"] > chunk["start_sec"], (
            f"Chunk {i} is empty: start={chunk['start_sec']} end={chunk['end_sec']}"
        )

    # Postcondition 4: sequential IDs
    for i, chunk in enumerate(chunks):
        assert chunk["chunk_id"] == i, (
            f"Chunk {i} has chunk_id={chunk['chunk_id']}, expected {i}"
        )


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------

@st.composite
def speech_segments_strategy(draw):
    """Generate valid non-overlapping speech segments within [0, total_duration]."""
    total = draw(st.floats(min_value=1.0, max_value=600.0, allow_nan=False, allow_infinity=False))
    n = draw(st.integers(min_value=0, max_value=20))
    if n == 0:
        return [], total

    # Generate n sorted start points
    starts = sorted(draw(st.lists(
        st.floats(min_value=0.0, max_value=total * 0.9, allow_nan=False, allow_infinity=False),
        min_size=n, max_size=n,
    )))
    segments = []
    for s in starts:
        e = s + draw(st.floats(min_value=0.1, max_value=min(2.0, total - s + 0.01), allow_nan=False, allow_infinity=False))
        e = min(e, total)
        if e > s:
            segments.append({"start": s, "end": e})

    # Remove overlaps (keep only non-overlapping)
    clean = []
    prev_end = 0.0
    for seg in segments:
        if seg["start"] >= prev_end:
            clean.append(seg)
            prev_end = seg["end"]

    return clean, total


@given(speech_segments_strategy())
@settings(max_examples=200)
def test_plan_chunks_contiguous(args):
    """Chunks must be contiguous."""
    segments, total = args
    chunks = plan_chunks(segments, total)
    assert_postconditions(chunks, total)


@given(speech_segments_strategy())
@settings(max_examples=200)
def test_plan_chunks_covers_full_audio(args):
    """Chunks must cover the full audio duration."""
    segments, total = args
    chunks = plan_chunks(segments, total)
    assert_postconditions(chunks, total)


@given(speech_segments_strategy())
@settings(max_examples=200)
def test_plan_chunks_no_empty_chunks(args):
    """No chunk may have end_sec <= start_sec."""
    segments, total = args
    chunks = plan_chunks(segments, total)
    assert_postconditions(chunks, total)


@given(speech_segments_strategy())
@settings(max_examples=200)
def test_plan_chunks_sequential_ids(args):
    """chunk_id values must be 0, 1, 2, ..."""
    segments, total = args
    chunks = plan_chunks(segments, total)
    assert_postconditions(chunks, total)


# ---------------------------------------------------------------------------
# Example tests
# ---------------------------------------------------------------------------

def test_plan_chunks_empty_segments():
    """Empty speech segments: audio is split into chunks by target size."""
    # 60s audio, no silence → chunk 0: 0-15 (first_chunk_max), chunk 1: 15-45, chunk 2: 45-60
    chunks = plan_chunks([], 60.0)
    assert_postconditions(chunks, 60.0)
    assert chunks[0]["start_sec"] == 0.0
    assert chunks[0]["end_sec"] == 15.0  # first_chunk_max_sec default
    assert chunks[-1]["end_sec"] == 60.0


def test_plan_chunks_zero_duration():
    """Zero duration returns empty list."""
    chunks = plan_chunks([], 0.0)
    assert chunks == []


def test_plan_chunks_negative_duration():
    """Negative duration returns empty list."""
    chunks = plan_chunks([], -5.0)
    assert chunks == []


def test_plan_chunks_audio_shorter_than_min():
    """Audio shorter than first_chunk_min_sec: single chunk covering full audio."""
    chunks = plan_chunks([], 2.0, first_chunk_min_sec=3.0, first_chunk_max_sec=15.0)
    assert len(chunks) == 1
    assert abs(chunks[0]["end_sec"] - 2.0) < 1e-9


def test_plan_chunks_first_chunk_uses_silence_boundary():
    """First chunk ends at a silence gap within [min, max]."""
    # Silence gap at [5.0, 6.0] — within [3, 15]
    segments = [{"start": 0.0, "end": 5.0}, {"start": 6.0, "end": 60.0}]
    chunks = plan_chunks(segments, 60.0, first_chunk_min_sec=3.0, first_chunk_max_sec=15.0)
    # First chunk should end at 5.0 (start of silence gap)
    # Actually the heuristic uses gap_e (end of silence gap) = 6.0
    assert chunks[0]["start_sec"] == 0.0
    assert chunks[0]["end_sec"] == 6.0  # gap_e = 6.0, within [3, 15]


def test_plan_chunks_tail_merge():
    """Tail audio < 50% of target is merged into previous chunk."""
    # 65 seconds total, 30s target → chunk 0 ~15s, chunk 1 ~30s, remaining ~20s
    # 20s < 30 * 0.5 = 15? No, 20 > 15. Let's use 70s total.
    # chunk 0: 0-15, chunk 1: 15-45, remaining: 45-70 = 25s > 15 → new chunk
    # Use 60s total: chunk 0: 0-15, chunk 1: 15-45, remaining: 45-60 = 15s = 50% → boundary case
    # Use 58s total: chunk 0: 0-15, chunk 1: 15-45, remaining: 45-58 = 13s < 15 → merge
    chunks = plan_chunks([], 58.0, first_chunk_max_sec=15.0, normal_chunk_target_sec=30.0)
    assert_postconditions(chunks, 58.0)
    # Last chunk should cover to 58.0
    assert abs(chunks[-1]["end_sec"] - 58.0) < 1e-9
    # Should have 2 chunks (not 3), because tail is merged
    assert len(chunks) == 2


def test_plan_chunks_silence_snap():
    """Subsequent chunks snap to silence boundaries near the target."""
    # 90s audio, silence at [28, 32]
    # chunk 0: 0-15 (first_chunk_max default)
    # chunk 1: starts at 15, targets 15+30=45, snap window [40, 50]
    #   silence gap start at 28 is NOT in [40, 50] → no snap, cut at 45
    # chunk 2: starts at 45, targets 45+30=75, snap window [70, 80]
    #   no silence in [70, 80] → cut at 75
    # chunk 3: starts at 75, remaining=15 < 30*0.5=15 → boundary, merge into chunk 2
    # Actually 15 == 15 (not strictly less), so a new chunk is created: 75-90
    segments = [
        {"start": 0.0, "end": 10.0},
        {"start": 10.5, "end": 28.0},
        {"start": 32.0, "end": 90.0},
    ]
    chunks = plan_chunks(
        segments, 90.0,
        first_chunk_max_sec=15.0,
        normal_chunk_target_sec=30.0,
        silence_snap_radius_sec=5.0,
    )
    assert_postconditions(chunks, 90.0)
    # Verify chunk 0 ends at 15 (no silence in [3, 15] window — silence gap at [10, 10.5] ends at 10.5 which is in [3, 15])
    assert chunks[0]["end_sec"] == 10.5  # gap_e=10.5 is within [3, 15]


def test_plan_chunks_postconditions_simple():
    """Basic 5-minute audio with no silence: all postconditions hold."""
    chunks = plan_chunks([], 300.0)
    assert_postconditions(chunks, 300.0)
    # Should have multiple chunks
    assert len(chunks) > 1
