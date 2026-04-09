from pathlib import Path

from autosmartcut.perception import (
    SpeechSegment,
    build_layer2_input_document,
    compact_annotations,
    write_perception_outputs,
    _annotations_from_segments,
)


def test_annotations_from_segments_is_speech_only_with_gap_after() -> None:
    segments = [
        SpeechSegment(t_start=0.0, t_end=1.0, content="第一句", chars=[]),
        SpeechSegment(t_start=1.8, t_end=3.0, content="第二句", chars=[]),
    ]
    anns = _annotations_from_segments(
        segments,
        duration=4.0,
        silence_threshold=0.8,
        include_char_timestamps=False,
    )

    assert len(anns) == 2
    assert all("type" not in a for a in anns)
    assert anns[0]["index"] == 0 and anns[1]["index"] == 1
    assert anns[0]["gap_after"] == 0.8
    assert anns[1]["gap_after"] == 1.0


def test_build_layer2_input_document_only_index_and_text() -> None:
    layer1_doc = {
        "source": "samples/a.mp4",
        "annotations": [
            {"index": 0, "content": "A"},
            {"index": 1, "content": "B"},
        ],
    }
    out = build_layer2_input_document(layer1_doc)
    assert out["source"] == "samples/a.mp4"
    assert out["tokens"] == [{"index": 0, "text": "A"}, {"index": 1, "text": "B"}]


def test_write_perception_outputs_writes_two_json_files(tmp_path: Path) -> None:
    layer1_doc = {"source": "x.mp4", "annotations": compact_annotations([{"index": 0, "t_start": 0.0, "t_end": 1.0, "content": "x", "gap_after": 0.2}])}
    layer2_doc = {"source": "x.mp4", "tokens": [{"index": 0, "text": "x"}]}
    p1, p2 = write_perception_outputs(layer1_doc, layer2_doc, tmp_path)
    assert p1.name == "layer1_annotations.json"
    assert p2.name == "layer2_input.json"
    assert p1.exists() and p2.exists()
