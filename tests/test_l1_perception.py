"""Layer 1（识别层 / perception）单测。依赖 torch 等 L1 运行时。"""

from autosmartcut.annotation_tokens import tokens_from_annotations
from autosmartcut.perception import (
    SpeechSegment,
    compact_annotations,
    _annotations_from_segments,
    segment_raw_text_only,
)


def test_annotations_from_segments_is_speech_only_with_gap_after() -> None:
    segments = [
        SpeechSegment(t_start=0.0, t_end=1.0, content="第一句", chars=[]),
        SpeechSegment(t_start=1.8, t_end=3.0, content="第二句", chars=[]),
    ]
    anns = _annotations_from_segments(
        segments,
        4.0,
        0.8,
        include_char_timestamps=False,
    )

    assert len(anns) == 2
    assert all("type" not in a for a in anns)
    assert anns[0]["index"] == 0 and anns[1]["index"] == 1
    assert anns[0]["gap_after"] == 0.8
    assert anns[1]["gap_after"] == 1.0


def test_tokens_from_annotations_matches_layer1_shape() -> None:
    layer1_doc = {
        "source": "samples/a.mp4",
        "annotations": [
            {"index": 0, "content": "A"},
            {"index": 1, "content": "B"},
        ],
    }
    out = tokens_from_annotations(layer1_doc["annotations"])
    assert out == [{"index": 0, "text": "A"}, {"index": 1, "text": "B"}]


def test_segment_raw_text_only_punctuation() -> None:
    spans = segment_raw_text_only("你好。世界！", set(), max_chars=200)
    assert len(spans) == 2
    assert spans[0].content == "你好"
    assert spans[1].content == "世界"
    assert spans[0].first_kept_ord == 0
    assert spans[0].last_kept_ord == 1


def test_compact_annotations_drops_char_metadata() -> None:
    ann = {
        "index": 0,
        "t_start": 0.0,
        "t_end": 1.0,
        "content": "x",
        "gap_after": 0.2,
        "confidence": 0.9,
        "metadata": {"char_timestamps": []},
    }
    compact = compact_annotations([ann])
    assert "metadata" not in compact[0]
    assert compact[0]["content"] == "x"
