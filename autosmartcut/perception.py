"""Layer 1 perception helpers.

ASR + alignment 产出句级 ``annotations[]``，由 ``run_perception_layer`` 写入
``timeline_manifest.json``（MVP-mini）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import av
import bisect
import numpy as np
import torch
from qwen_asr.inference.qwen3_asr import Qwen3ASRModel

from autosmartcut.config import AppConfig
from autosmartcut.manifest_io import load_manifest, save_manifest, touch_layer_status
from autosmartcut.pipeline_run import PipelineRun

logger = logging.getLogger(__name__)

_ASR_SAMPLE_RATE = 16000

# L1 写出、L3 VAD 复用的 16kHz mono float32 WAV（相对 ``timeline_manifest.json`` 所在目录）
AUDIO_16K_WAV_NAME = "audio_16k.wav"


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
	"""Decode any supported media file into mono float32 PCM at the given rate.

	保留作 fallback；主路径使用 :func:`extract_audio_16k_wav` + :func:`read_audio_16k_wav`
	以降低峰值内存。
	"""
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
	# fltp 已是 float32，无需 astype 再拷贝一份
	return np.concatenate(chunks), sample_rate


def extract_audio_16k_wav(
	media_path: Path,
	wav_path: Path,
	*,
	sample_rate: int = _ASR_SAMPLE_RATE,
	audio_stream_index: int = 0,
) -> None:
	"""PyAV 边解码边写 16kHz mono float32 WAV，避免在内存中堆积整条 PCM。"""
	import soundfile as sf

	wav_path.parent.mkdir(parents=True, exist_ok=True)
	container = av.open(str(media_path))
	resampler = av.AudioResampler(format="fltp", layout="mono", rate=sample_rate)
	try:
		with sf.SoundFile(
			str(wav_path),
			mode="w",
			samplerate=sample_rate,
			channels=1,
			format="WAV",
			subtype="FLOAT",
		) as wav_out:
			for frame in container.decode(audio=audio_stream_index):
				for out_frame in resampler.resample(frame):
					wav_out.write(out_frame.to_ndarray()[0])
			for out_frame in resampler.resample(None):
				wav_out.write(out_frame.to_ndarray()[0])
	finally:
		container.close()


def read_audio_16k_wav(wav_path: Path) -> tuple[np.ndarray, int]:
	"""读取 L1 产出的 16k mono float32 WAV，供 ASR 与本地校验。"""
	import soundfile as sf

	audio, sr = sf.read(str(wav_path), dtype="float32", always_2d=False)
	audio = np.asarray(audio, dtype=np.float32)
	if audio.ndim > 1:
		audio = np.mean(audio, axis=-1).astype(np.float32)
	return audio, int(sr)


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
	_silence_threshold_unused: float,
	*,
	include_char_timestamps: bool,
) -> list[dict[str, Any]]:
	annotations: list[dict[str, Any]] = []

	if not segments:
		return annotations

	for idx, seg in enumerate(segments):
		if idx + 1 < len(segments):
			nxt = segments[idx + 1]
			gap_after = max(0.0, float(nxt.t_start - seg.t_end))
		elif duration > 0:
			gap_after = max(0.0, float(duration - seg.t_end))
		else:
			gap_after = 0.0

		item: dict[str, Any] = {
			"index": idx,
			"t_start": seg.t_start,
			"t_end": seg.t_end,
			"content": seg.content,
			"gap_after": gap_after,
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
				"content": ann.get("content", ""),
				"gap_after": float(ann.get("gap_after", 0.0)),
				"confidence": ann.get("confidence", 1.0),
			}
		)
	return result


def _torch_dtype(dtype_name: str) -> torch.dtype:
	mapping = {
		"float16": torch.float16,
		"bfloat16": torch.bfloat16,
		"float32": torch.float32,
	}
	return mapping[dtype_name]


def _build_qwen3_asr_model(
	*,
	asr_model_path: Path,
	forced_aligner_path: Path,
	backend: str,
	device: str,
	dtype: torch.dtype,
	gpu_memory_utilization: float,
) -> Qwen3ASRModel:
	asr_path = str(asr_model_path)
	forced_path = str(forced_aligner_path)
	if backend == "transformers":
		return Qwen3ASRModel.from_pretrained(
			pretrained_model_name_or_path=asr_path,
			forced_aligner=forced_path,
			forced_aligner_kwargs={
				"dtype": dtype,
				"device_map": device,
			},
			dtype=dtype,
			device_map=device,
			max_new_tokens=1024,
		)
	return Qwen3ASRModel.LLM(
		model=asr_path,
		forced_aligner=forced_path,
		forced_aligner_kwargs={
			"dtype": dtype,
			"device_map": device,
		},
		gpu_memory_utilization=gpu_memory_utilization,
		max_new_tokens=1024,
	)


def run_perception_layer(
	run: PipelineRun,
	*,
	asr_model_path: Path,
	forced_aligner_path: Path,
	config: AppConfig | None = None,
	backend: str = "transformers",
	device: str = "cuda:0",
	dtype: str = "float16",
	language: str = "Chinese",
	include_char_timestamps: bool = True,
	gpu_memory_utilization: float = 0.8,
	split_pause_threshold: float | None = None,
	silence_threshold: float | None = None,
	max_chars: int | None = None,
	segmentation_mode: str | None = None,
) -> None:
	"""L1 端到端：视频 ASR → 写入 ``timeline_manifest.json`` 的 ``annotations[]``。"""
	from autosmartcut.log import ensure_autosmartcut_logging

	ensure_autosmartcut_logging(verbose=False)

	if config is None:
		from autosmartcut.config import load_config

		config = load_config()

	perception_config = config.perception
	split_pause = (
		split_pause_threshold
		if split_pause_threshold is not None
		else perception_config.split_pause_threshold
	)
	silence_thr = (
		silence_threshold
		if silence_threshold is not None
		else perception_config.silence_threshold
	)
	max_c = max_chars if max_chars is not None else perception_config.max_chars
	sentence_endings = set(perception_config.sentence_endings)
	seg_mode = (
		segmentation_mode
		if segmentation_mode is not None
		else getattr(perception_config, "segmentation_mode", "punctuation")
	)

	if not run.video_path.exists():
		raise FileNotFoundError(f"输入视频不存在: {run.video_path}")
	if not asr_model_path.exists():
		raise FileNotFoundError(f"ASR 模型目录不存在: {asr_model_path}")
	if not forced_aligner_path.exists():
		raise FileNotFoundError(f"Forced aligner 目录不存在: {forced_aligner_path}")

	logger.info("[L1] 开始识别层 backend=%s", backend)
	torch_dtype = _torch_dtype(dtype)
	model = _build_qwen3_asr_model(
		asr_model_path=asr_model_path,
		forced_aligner_path=forced_aligner_path,
		backend=backend,
		device=device,
		dtype=torch_dtype,
		gpu_memory_utilization=gpu_memory_utilization,
	)

	wav_cache = run.output_dir / AUDIO_16K_WAV_NAME
	logger.info("[L1] PyAV 解码音频 → %s", wav_cache)
	extract_audio_16k_wav(run.video_path, wav_cache)
	audio_arr, audio_sr = read_audio_16k_wav(wav_cache)
	logger.info(
		"[L1] 音频就绪 %.1fs @ %dHz", audio_arr.shape[0] / audio_sr, audio_sr
	)

	results = model.transcribe(
		audio=(audio_arr, audio_sr),
		language=language,
		return_time_stamps=True,
	)
	if not results:
		raise RuntimeError("ASR 返回空结果")

	transcription = results[0]
	raw_text = getattr(transcription, "text", "")
	lang_out = getattr(transcription, "language", "") or language
	duration = duration_seconds(run.video_path)

	full_doc = build_layer1_document(
		source=str(run.video_path),
		language=lang_out,
		raw_text=raw_text,
		transcription=transcription,
		duration=duration,
		segmentation_mode=seg_mode,
		split_pause_threshold=split_pause,
		silence_threshold=silence_thr,
		max_chars=max_c,
		sentence_endings=sentence_endings,
		include_char_timestamps=include_char_timestamps,
	)
	light_doc = {
		"source": full_doc["source"],
		"language": full_doc["language"],
		"raw_text": full_doc["raw_text"],
		"annotations": compact_annotations(full_doc["annotations"]),
	}

	data = load_manifest(run.manifest_path)
	data["source"] = light_doc["source"]
	data["language"] = light_doc["language"]
	data["raw_text"] = light_doc["raw_text"]
	data["annotations"] = light_doc["annotations"]
	sm = data.setdefault("source_media", {})
	if isinstance(sm, dict):
		sm["path"] = light_doc["source"]
		sm["duration"] = float(duration)
		# 相对 manifest 父目录，供 L3 VAD 复用，避免二次整轨解码
		sm["audio_16k_path"] = AUDIO_16K_WAV_NAME
	touch_layer_status(data, "l1")
	save_manifest(run.manifest_path, data, atomic=True)

	n = len(light_doc["annotations"])
	logger.info("[L1] 完成 segmentation=%s 标注数=%d → %s", seg_mode, n, run.manifest_path)
