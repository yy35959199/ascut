"""Layer 1 perception helpers.

This module contains the production logic for transforming ASR + alignment
output into the JSON1/JSON2 documents used by later stages.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import av
import bisect
import numpy as np

_ASR_SAMPLE_RATE = 16000


@dataclass(frozen=True)
class CharItem:
	text: str
	start: float
	end: float


@dataclass(frozen=True)
class SpeechSegment:
	t_start: float
	t_end: float
	content: str
	chars: list[CharItem]


def load_audio_mono(path: Path, sample_rate: int = _ASR_SAMPLE_RATE) -> tuple[np.ndarray, int]:
	"""Decode any supported media file into mono float32 PCM at the given rate."""
	container = av.open(str(path))
	resampler = av.AudioResampler(format="fltp", layout="mono", rate=sample_rate)
	chunks: list[np.ndarray] = []
	try:
		for frame in container.decode(audio=0):
			for out_frame in resampler.resample(frame):
				arr = out_frame.to_ndarray()
				chunks.append(arr[0])
		for out_frame in resampler.resample(None):
			arr = out_frame.to_ndarray()
			chunks.append(arr[0])
	finally:
		container.close()
	if not chunks:
		return np.zeros(0, dtype=np.float32), sample_rate
	audio = np.concatenate(chunks).astype(np.float32)
	return audio, sample_rate


def duration_seconds(media_path: Path) -> float:
	with av.open(str(media_path)) as container:
		if container.duration is None:
			return 0.0
		return float(container.duration / av.time_base)


def transcription_to_char_items(transcription: Any) -> list[CharItem]:
	time_stamps = getattr(transcription, "time_stamps", None)
	if time_stamps is None:
		return []

	items = getattr(time_stamps, "items", [])
	char_items: list[CharItem] = []
	for item in items:
		text = str(getattr(item, "text", ""))
		start = float(getattr(item, "start_time", 0.0))
		end = float(getattr(item, "end_time", 0.0))
		if end < start:
			continue
		char_items.append(CharItem(text=text, start=start, end=end))

	char_items.sort(key=lambda x: x.start)
	return char_items


def split_sentence_segments_by_timing(
	chars: Sequence[CharItem],
	split_pause_threshold: float,
	max_chars: int,
	sentence_endings: set[str],
) -> list[SpeechSegment]:
	if not chars:
		return []

	segments: list[SpeechSegment] = []
	buf: list[CharItem] = []

	def flush() -> None:
		nonlocal buf
		if not buf:
			return
		text = "".join(c.text for c in buf).strip()
		if text:
			segments.append(
				SpeechSegment(
					t_start=buf[0].start,
					t_end=buf[-1].end,
					content=text,
					chars=list(buf),
				)
			)
		buf = []

	prev: CharItem | None = None
	for ch in chars:
		if prev is not None and ch.start - prev.end >= split_pause_threshold and buf:
			flush()

		buf.append(ch)
		text_len = sum(1 for item in buf if item.text.strip())

		if ch.text in sentence_endings or text_len >= max_chars:
			flush()

		prev = ch

	flush()
	return segments


def split_sentence_segments_by_punctuation(
	chars: Sequence[CharItem],
	raw_text: str,
	sentence_endings: set[str],
	max_chars: int,
) -> list[SpeechSegment]:
	"""Split by punctuation in raw_text and project boundaries back to char timestamps."""
	if not chars:
		return []

	_ = "".join(c.text for c in chars)
	item_starts: list[int] = []
	acc = 0
	for item in chars:
		item_starts.append(acc)
		acc += len(item.text)

	segments: list[SpeechSegment] = []
	cur_item_idx = 0
	punct_set = set(sentence_endings) | {".", ",", "!", "?", ":", ";", "、", "，", "。", "！", "？", "：", "；"}
	flat_pos = 0

	def flush_range(s_idx: int, e_idx: int) -> None:
		if s_idx > e_idx:
			return
		buf: list[CharItem] = []
		buf_chars = 0
		for item in chars[s_idx : e_idx + 1]:
			item_len = len(item.text)
			if buf and buf_chars + item_len > max_chars:
				segments.append(
					SpeechSegment(
						t_start=buf[0].start,
						t_end=buf[-1].end,
						content="".join(x.text for x in buf).strip(),
						chars=list(buf),
					)
				)
				buf = []
				buf_chars = 0
			buf.append(item)
			buf_chars += item_len
		if buf:
			segments.append(
				SpeechSegment(
					t_start=buf[0].start,
					t_end=buf[-1].end,
					content="".join(x.text for x in buf).strip(),
					chars=list(buf),
				)
			)

	for ch in raw_text:
		if ch.isspace():
			continue
		if ch in punct_set:
			if flat_pos == 0:
				continue
			boundary_flat_idx = flat_pos - 1
			item_idx = bisect.bisect_right(item_starts, boundary_flat_idx) - 1
			if item_idx < cur_item_idx:
				continue
			flush_range(cur_item_idx, item_idx)
			cur_item_idx = item_idx + 1
		else:
			flat_pos += 1

	if cur_item_idx < len(chars):
		flush_range(cur_item_idx, len(chars) - 1)

	return segments


def infer_segments(
	transcription: Any,
	*,
	segmentation_mode: str,
	split_pause_threshold: float,
	max_chars: int,
	sentence_endings: Sequence[str],
) -> list[SpeechSegment]:
	chars = transcription_to_char_items(transcription)
	endings = set(sentence_endings)
	if segmentation_mode == "punctuation":
		raw_text = str(getattr(transcription, "text", ""))
		if raw_text and chars:
			return split_sentence_segments_by_punctuation(chars, raw_text, endings, max_chars)
	return split_sentence_segments_by_timing(chars, split_pause_threshold, max_chars, endings)


def _annotations_from_segments(
	segments: Sequence[SpeechSegment],
	duration: float,
	silence_threshold: float,
	*,
	include_char_timestamps: bool,
) -> list[dict[str, Any]]:
	annotations: list[dict[str, Any]] = []

	if not segments:
		if duration > 0:
			annotations.append(
				{
					"index": len(annotations),
					"t_start": 0.0,
					"t_end": duration,
					"type": "silence",
					"content": "",
					"confidence": 1.0,
					"metadata": {},
				}
			)
		return annotations

	first = segments[0]
	if first.t_start >= silence_threshold:
		annotations.append(
			{
				"index": len(annotations),
				"t_start": 0.0,
				"t_end": first.t_start,
				"type": "silence",
				"content": "",
				"confidence": 1.0,
				"metadata": {},
			}
		)

	for idx, seg in enumerate(segments):
		item: dict[str, Any] = {
			"index": len(annotations),
			"t_start": seg.t_start,
			"t_end": seg.t_end,
			"type": "speech",
			"content": seg.content,
			"confidence": 1.0,
		}
		if include_char_timestamps:
			item["metadata"] = {
				"char_timestamps": [
					{"text": c.text, "start": c.start, "end": c.end}
					for c in seg.chars
				]
			}
		else:
			item["metadata"] = {}
		annotations.append(item)

		if idx + 1 < len(segments):
			nxt = segments[idx + 1]
			gap = nxt.t_start - seg.t_end
			if gap >= silence_threshold:
				annotations.append(
					{
						"index": len(annotations),
						"t_start": seg.t_end,
						"t_end": nxt.t_start,
						"type": "silence",
						"content": "",
						"confidence": 1.0,
						"metadata": {},
					}
				)

	last = segments[-1]
	if duration > 0 and duration - last.t_end >= silence_threshold:
		annotations.append(
			{
				"index": len(annotations),
				"t_start": last.t_end,
				"t_end": duration,
				"type": "silence",
				"content": "",
				"confidence": 1.0,
				"metadata": {},
			}
		)

	return annotations


def build_layer1_document(
	*,
	source: str,
	language: str,
	raw_text: str,
	transcription: Any,
	duration: float,
	segmentation_mode: str,
	split_pause_threshold: float,
	silence_threshold: float,
	max_chars: int,
	sentence_endings: Sequence[str],
	include_char_timestamps: bool,
) -> dict[str, Any]:
	segments = infer_segments(
		transcription,
		segmentation_mode=segmentation_mode,
		split_pause_threshold=split_pause_threshold,
		max_chars=max_chars,
		sentence_endings=sentence_endings,
	)
	annotations = _annotations_from_segments(
		segments,
		duration,
		silence_threshold,
		include_char_timestamps=include_char_timestamps,
	)
	return {
		"source": source,
		"language": language,
		"raw_text": raw_text,
		"annotations": annotations,
	}


def compact_annotations(annotations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
	"""Create a compact, index-stable view that drops char-level metadata."""
	result: list[dict[str, Any]] = []
	for i, ann in enumerate(annotations):
		result.append(
			{
				"index": int(ann.get("index", i)),
				"t_start": ann["t_start"],
				"t_end": ann["t_end"],
				"type": ann["type"],
				"content": ann.get("content", ""),
				"confidence": ann.get("confidence", 1.0),
			}
		)
	return result


def build_layer2_input_document(layer1_document: Mapping[str, Any]) -> dict[str, Any]:
	"""Convert layer1 annotations into the aligned layer2 input document."""
	annotations = list(layer1_document.get("annotations", []))
	tokens: list[dict[str, Any]] = []
	for i, ann in enumerate(annotations):
		is_speech = ann.get("type") == "speech"
		tokens.append(
			{
				"index": int(ann.get("index", i)),
				"type": ann.get("type", ""),
				"text": ann.get("content") if is_speech else None,
				"selectable": is_speech,
			}
		)
	return {
		"source": layer1_document.get("source", ""),
		"tokens": tokens,
	}
