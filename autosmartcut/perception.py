"""Layer 1 perception helpers.

ASR + alignment 产出句级 ``annotations[]``，由 ``run_perception_layer`` 写入
``timeline_manifest.json``（MVP-mini）。
"""
from __future__ import annotations

import copy
import logging
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import av
import bisect
import numpy as np
import torch
from qwen_asr.inference.qwen3_asr import Qwen3ASRModel
from qwen_asr.inference.qwen3_forced_aligner import Qwen3ForcedAligner

from autosmartcut.config import AppConfig
from autosmartcut.log import (
	log_lazy_json,
	log_stage,
	log_stage_result,
	setup_logging_for_manifest,
)
from autosmartcut.manifest_io import (
	load_manifest,
	save_manifest,
	touch_layer_status,
	validate_manifest_for_l1b,
)
from autosmartcut.pipeline_run import PipelineRun

logger = logging.getLogger(__name__)

_ASR_SAMPLE_RATE = 16000

# L1 写出、L3 VAD 复用的 16kHz mono float32 WAV（相对 ``timeline_manifest.json`` 所在目录）
AUDIO_16K_WAV_NAME = "audio_16k.wav"


def aligner_is_kept_char(ch: str) -> bool:
	"""与 ``Qwen3ForceAlignProcessor.is_kept_char`` 一致：字母/数字/撇号。"""
	if ch == "'":
		return True
	cat = unicodedata.category(ch)
	return cat.startswith("L") or cat.startswith("N")


def default_punct_set(sentence_endings: set[str]) -> set[str]:
	return set(sentence_endings) | {
		".",
		",",
		"!",
		"?",
		":",
		";",
		"、",
		"，",
		"。",
		"！",
		"？",
		"：",
		"；",
	}


@dataclass(frozen=True)
class SentenceSpan:
	"""纯文本分句结果（L1A/L1B 共用 SSOT）。"""

	index: int
	raw_start: int
	raw_end: int
	content: str
	first_kept_ord: int | None
	last_kept_ord: int | None


@dataclass(frozen=True)
class SentenceTiming:
	index: int
	t_start: float | None
	t_end: float | None


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
		ga = ann.get("gap_after")
		result.append(
			{
				"index": int(ann.get("index", i)),
				"t_start": ann.get("t_start"),
				"t_end": ann.get("t_end"),
				"content": ann.get("content", ""),
				"gap_after": float(ga) if ga is not None else None,
				"confidence": ann.get("confidence", 1.0),
			}
		)
	return result


def count_kept_chars_in_raw(raw_text: str) -> int:
	return sum(1 for ch in raw_text if aligner_is_kept_char(ch))


def segment_raw_text_only(
	raw_text: str,
	sentence_endings: set[str],
	max_chars: int,
) -> list[SentenceSpan]:
	"""仅依赖 ``raw_text`` 的分句（与强制对齐 ``word_list`` 的 kept 计数系一致）。"""
	punct = default_punct_set(sentence_endings)
	spans: list[SentenceSpan] = []
	buf: list[tuple[int, str]] = []
	sent_kept = 0
	global_kept = 0
	first_k: int | None = None
	last_k: int | None = None

	def flush_span() -> None:
		nonlocal buf, sent_kept, first_k, last_k
		if not buf:
			return
		raw_start = buf[0][0]
		raw_end = buf[-1][0] + 1
		content = "".join(c for _, c in buf).strip()
		if not content:
			buf = []
			sent_kept = 0
			first_k = None
			last_k = None
			return
		idx = len(spans)
		spans.append(
			SentenceSpan(
				index=idx,
				raw_start=raw_start,
				raw_end=raw_end,
				content=content,
				first_kept_ord=first_k,
				last_kept_ord=last_k,
			)
		)
		buf = []
		sent_kept = 0
		first_k = None
		last_k = None

	for i, ch in enumerate(raw_text):
		if ch.isspace():
			continue
		if ch in punct:
			flush_span()
			continue
		if aligner_is_kept_char(ch):
			if first_k is None:
				first_k = global_kept
			last_k = global_kept
			global_kept += 1
			sent_kept += 1
		buf.append((i, ch))
		if sent_kept >= max_chars and aligner_is_kept_char(ch):
			flush_span()
	flush_span()
	return spans


def _item_time_start_end(item: Any) -> tuple[float, float]:
	s = float(getattr(item, "start_time", 0.0))
	e = float(getattr(item, "end_time", 0.0))
	return s, e


def assign_times_to_spans(
	spans: Sequence[SentenceSpan],
	align_items: Sequence[Any],
	*,
	overlap_tolerance: float = 0.05,
) -> list[SentenceTiming]:
	"""将 ``align_items``（与 ``raw_text`` kept 流等长拼接）映射到各句起止时间。"""
	item_starts: list[int] = []
	acc = 0
	for it in align_items:
		item_starts.append(acc)
		acc += len(str(getattr(it, "text", "")))
	total_items_kept = acc
	if total_items_kept == 0 and not spans:
		return []

	timings: list[SentenceTiming] = []
	for sp in spans:
		if sp.first_kept_ord is None or sp.last_kept_ord is None:
			timings.append(SentenceTiming(sp.index, None, None))
			continue
		i0 = bisect.bisect_right(item_starts, sp.first_kept_ord) - 1
		i1 = bisect.bisect_right(item_starts, sp.last_kept_ord) - 1
		i0 = max(0, min(i0, len(align_items) - 1))
		i1 = max(0, min(i1, len(align_items) - 1))
		ts, _ = _item_time_start_end(align_items[i0])
		_, te = _item_time_start_end(align_items[i1])
		if ts > te:
			raise ValueError(f"句 {sp.index} 时间逆序: t_start={ts} t_end={te}")
		timings.append(SentenceTiming(sp.index, ts, te))

	for a, b in zip(timings, timings[1:]):
		if (
			a.t_end is not None
			and b.t_start is not None
			and b.t_start < a.t_end - overlap_tolerance
		):
			raise ValueError(
				f"句间时间重叠过大: index {a.index} t_end={a.t_end} 与 index {b.index} t_start={b.t_start}"
			)
	return timings


def validate_index_text_against_spans(
	annotations: Sequence[Mapping[str, Any]],
	spans: Sequence[SentenceSpan],
) -> None:
	if len(annotations) != len(spans):
		raise ValueError(
			f"annotations 条数与分句不一致: {len(annotations)} != {len(spans)}"
		)
	for ann, sp in zip(annotations, spans):
		if int(ann.get("index", -1)) != sp.index:
			raise ValueError(f"index 不一致: ann={ann.get('index')} span={sp.index}")
		if str(ann.get("content", "")) != sp.content:
			raise ValueError(
				f"content 不一致 index={sp.index}: ann={ann.get('content')!r} span={sp.content!r}"
			)


def validate_alignment_kept_match(raw_text: str, align_items: Sequence[Any]) -> None:
	want = count_kept_chars_in_raw(raw_text)
	got = sum(len(str(getattr(it, "text", ""))) for it in align_items)
	if want != got:
		raise ValueError(
			f"对齐 kept 长度与原文不一致 (V1): raw_kept={want} align_tokens={got}"
		)


def validate_align_items_monotonic(align_items: Sequence[Any]) -> None:
	prev: float | None = None
	for i, it in enumerate(align_items):
		s, e = _item_time_start_end(it)
		if e < s:
			raise ValueError(f"align_items[{i}] 时间逆序: start={s} end={e}")
		if prev is not None and s < prev - 1e-6:
			raise ValueError(f"align_items 时间非单调: [{i}] start={s} prev_end≈{prev}")
		prev = e


def _annotations_l1a_from_spans(spans: Sequence[SentenceSpan]) -> list[dict[str, Any]]:
	out: list[dict[str, Any]] = []
	for sp in spans:
		out.append(
			{
				"index": sp.index,
				"t_start": None,
				"t_end": None,
				"content": sp.content,
				"gap_after": None,
				"confidence": 1.0,
				"metadata": {},
			}
		)
	return out


def build_l1_contract(
	*,
	segmentation_mode: str,
	sentence_endings: Sequence[str],
	max_chars: int,
	language: str,
) -> dict[str, Any]:
	return {
		"text_norm_version": "v1",
		"segmentation_mode": segmentation_mode,
		"sentence_endings": list(sentence_endings),
		"max_chars": int(max_chars),
		"asr_language": language,
	}


def _merge_align_items_with_offset(
	items: Sequence[Any], offset_sec: float
) -> list[Any]:
	"""返回带 float start/end 的简单 namespace 式对象列表（仅用于后续映射）。"""
	from types import SimpleNamespace

	out: list[Any] = []
	for it in items:
		s, e = _item_time_start_end(it)
		out.append(
			SimpleNamespace(
				text=str(getattr(it, "text", "")),
				start_time=s + offset_sec,
				end_time=e + offset_sec,
			)
		)
	return out


def _forced_align_chunked(
	aligner: Qwen3ForcedAligner,
	raw_text: str,
	audio_arr: np.ndarray,
	audio_sr: int,
	language: str,
) -> list[Any]:
	"""长音频按 ``MAX_FORCE_ALIGN_INPUT_SECONDS`` 分块对齐并合并 items（V6）。"""
	from qwen_asr.inference.utils import MAX_FORCE_ALIGN_INPUT_SECONDS, split_audio_into_chunks

	chunks = split_audio_into_chunks(
		audio_arr, sr=audio_sr, max_chunk_sec=float(MAX_FORCE_ALIGN_INPUT_SECONDS)
	)
	if len(chunks) == 1:
		res = aligner.align(audio=(audio_arr, audio_sr), text=raw_text, language=language)
		return list(res[0])

	total_kept = count_kept_chars_in_raw(raw_text)
	if total_kept == 0:
		res = aligner.align(audio=(audio_arr, audio_sr), text=raw_text, language=language)
		return list(res[0])

	# 按 kept 字符比例切分 raw_text，尽量落在标点处
	kept_ord_to_raw_end: list[int] = []
	g = 0
	for i, ch in enumerate(raw_text):
		if aligner_is_kept_char(ch):
			g += 1
			kept_ord_to_raw_end.append(i + 1)
	n = len(chunks)
	text_chunks: list[str] = []
	prev_cut = 0
	punct = default_punct_set(set())
	for ci in range(n - 1):
		target = max(1, min(total_kept - 1, int(round((ci + 1) * total_kept / n))))
		raw_hi = kept_ord_to_raw_end[target - 1]
		best = raw_hi
		for j in range(raw_hi - 1, prev_cut - 1, -1):
			if j < 0:
				break
			if raw_text[j] in punct:
				best = j + 1
				break
		if best <= prev_cut:
			best = raw_hi
		text_chunks.append(raw_text[prev_cut:best].strip())
		prev_cut = best
	text_chunks.append(raw_text[prev_cut:].strip())
	if len(text_chunks) != n:
		raise RuntimeError("内部分句块数与音频块数不一致")
	for tc in text_chunks:
		if not tc.strip():
			raise ValueError("长音频分块后某段文本为空，无法对齐；请缩短单段或调整分句参数")

	merged: list[Any] = []
	for (cwav, offset_sec), tchunk in zip(chunks, text_chunks):
		res = aligner.align(audio=(cwav, audio_sr), text=tchunk, language=language)
		merged.extend(_merge_align_items_with_offset(list(res[0]), offset_sec))
	return merged


def _resolve_perception_params(
	config: AppConfig,
	*,
	split_pause_threshold: float | None,
	silence_threshold: float | None,
	max_chars: int | None,
	segmentation_mode: str | None,
) -> tuple[float, float, int, set[str], str]:
	pc = config.perception
	split_pause = (
		split_pause_threshold
		if split_pause_threshold is not None
		else pc.split_pause_threshold
	)
	silence_thr = (
		silence_threshold if silence_threshold is not None else pc.silence_threshold
	)
	max_c = max_chars if max_chars is not None else pc.max_chars
	sentence_endings = set(pc.sentence_endings)
	seg_mode = segmentation_mode if segmentation_mode is not None else getattr(pc, "segmentation_mode", "punctuation")
	return split_pause, silence_thr, max_c, sentence_endings, seg_mode


def _build_qwen3_asr_model_asr_only(
	*,
	asr_model_path: Path,
	backend: str,
	device: str,
	dtype: torch.dtype,
	gpu_memory_utilization: float,
) -> Qwen3ASRModel:
	asr_path = str(asr_model_path)
	if backend == "transformers":
		return Qwen3ASRModel.from_pretrained(
			pretrained_model_name_or_path=asr_path,
			forced_aligner=None,
			dtype=dtype,
			device_map=device,
			max_new_tokens=1024,
		)
	return Qwen3ASRModel.LLM(
		model=asr_path,
		forced_aligner=None,
		gpu_memory_utilization=gpu_memory_utilization,
		max_new_tokens=1024,
	)


def _build_forced_aligner_only(
	*,
	forced_aligner_path: Path,
	device: str,
	dtype: torch.dtype,
	backend: str = "transformers",
) -> Qwen3ForcedAligner:
	# ForcedAligner 是 NAR 模型，qwen-asr 包只提供 from_pretrained 接口，无 vLLM 入口。
	# Qwen3ASRModel.LLM() 里的 forced_aligner= 参数底层也是 transformers 加载的。
	# 因此无论 backend 传什么，这里始终走 transformers。
	if backend == "vllm":
		logger.warning(
			"[L1B] ForcedAligner 不支持 vLLM 后端（NAR 模型），自动降级为 transformers。"
		)
	return Qwen3ForcedAligner.from_pretrained(
		str(forced_aligner_path),
		dtype=dtype,
		device_map=device,
	)


def run_l1a_asr_only(
	run: PipelineRun,
	*,
	asr_model_path: Path,
	config: AppConfig | None = None,
	backend: str = "transformers",
	device: str = "cuda:0",
	dtype: str = "float16",
	language: str = "Chinese",
	gpu_memory_utilization: float = 0.8,
	split_pause_threshold: float | None = None,
	silence_threshold: float | None = None,
	max_chars: int | None = None,
	segmentation_mode: str | None = None,
) -> None:
	"""L1A：ASR 文本定稿，``annotations`` 无时间字段。"""
	setup_logging_for_manifest(run.manifest_path, verbose=False)
	if config is None:
		from autosmartcut.config import load_config

		config = load_config()
	_, _, max_c, sentence_endings, seg_mode = _resolve_perception_params(
		config,
		split_pause_threshold=split_pause_threshold,
		silence_threshold=silence_threshold,
		max_chars=max_chars,
		segmentation_mode=segmentation_mode,
	)
	if seg_mode != "punctuation":
		raise ValueError("L1A/L1B 解耦路径暂仅支持 segmentation_mode=punctuation")
	if not run.video_path.exists():
		raise FileNotFoundError(f"输入视频不存在: {run.video_path}")
	if not asr_model_path.exists():
		raise FileNotFoundError(f"ASR 模型目录不存在: {asr_model_path}")

	torch_dtype = _torch_dtype(dtype)
	with log_stage(
		"l1a.load_asr_model",
		backend=backend,
		device=device,
		dtype=dtype,
		asr_model_path=str(asr_model_path),
	):
		model = _build_qwen3_asr_model_asr_only(
			asr_model_path=asr_model_path,
			backend=backend,
			device=device,
			dtype=torch_dtype,
			gpu_memory_utilization=gpu_memory_utilization,
		)

	wav_cache = run.output_dir / AUDIO_16K_WAV_NAME
	with log_stage(
		"l1a.audio_transcode",
		video=str(run.video_path),
		wav_out=str(wav_cache),
	):
		extract_audio_16k_wav(run.video_path, wav_cache)
	audio_arr, audio_sr = read_audio_16k_wav(wav_cache)
	logger.info(
		"[L1A] 音频就绪 %.1fs @ %dHz", audio_arr.shape[0] / audio_sr, audio_sr
	)

	with log_stage(
		"l1a.asr_transcribe",
		language=language,
		audio_samples=int(audio_arr.shape[0]),
		sample_rate=int(audio_sr),
	):
		results = model.transcribe(
			audio=(audio_arr, audio_sr),
			language=language,
			return_time_stamps=False,
		)
	if not results:
		raise RuntimeError("ASR 返回空结果")
	transcription = results[0]
	raw_text = str(getattr(transcription, "text", ""))
	lang_out = str(getattr(transcription, "language", "") or language)
	duration = duration_seconds(run.video_path)

	spans = segment_raw_text_only(raw_text, sentence_endings, max_c)
	ann_full = _annotations_l1a_from_spans(spans)
	light_doc = {
		"source": str(run.video_path),
		"language": lang_out,
		"raw_text": raw_text,
		"annotations": compact_annotations(ann_full),
	}
	contract = build_l1_contract(
		segmentation_mode=seg_mode,
		sentence_endings=sorted(sentence_endings),
		max_chars=max_c,
		language=language,
	)

	data = load_manifest(run.manifest_path)
	data["source"] = light_doc["source"]
	data["language"] = light_doc["language"]
	data["raw_text"] = light_doc["raw_text"]
	data["annotations"] = light_doc["annotations"]
	data["l1_contract"] = contract
	sm = data.setdefault("source_media", {})
	if isinstance(sm, dict):
		sm["path"] = light_doc["source"]
		sm["duration"] = float(duration)
		sm["audio_16k_path"] = AUDIO_16K_WAV_NAME
	with log_stage("l1a.persist_manifest", manifest=str(run.manifest_path)):
		touch_layer_status(data, "l1a")
		save_manifest(run.manifest_path, data, atomic=True)

	log_stage_result(
		"l1a.output",
		summary=f"annotations={len(light_doc['annotations'])} manifest={run.manifest_path}",
	)
	logger.info("[L1A] 完成 annotations=%d → %s", len(light_doc["annotations"]), run.manifest_path)


def compute_l1b_aligned_annotations(
	run: PipelineRun,
	manifest_data: dict[str, Any],
	*,
	forced_aligner_path: Path,
	config: AppConfig,
	backend: str = "transformers",
	device: str = "cuda:0",
	dtype: str = "float16",
	language: str | None = None,
	gpu_memory_utilization: float = 0.8,
	split_pause_threshold: float | None = None,
	silence_threshold: float | None = None,
	max_chars: int | None = None,
	segmentation_mode: str | None = None,
) -> list[dict[str, Any]]:
	"""纯计算：强制对齐并回填时间轴；不写盘。返回 ``compact_annotations`` 后的列表。"""
	raw_text = str(manifest_data.get("raw_text", ""))
	annotations = manifest_data.get("annotations")
	if not isinstance(annotations, list) or not annotations:
		raise ValueError("L1B 需要非空 annotations[]")

	_split_pause, _silence_thr, max_c, sentence_endings, seg_mode = _resolve_perception_params(
		config,
		split_pause_threshold=split_pause_threshold,
		silence_threshold=silence_threshold,
		max_chars=max_chars,
		segmentation_mode=segmentation_mode,
	)
	contract = manifest_data.get("l1_contract")
	if isinstance(contract, dict):
		mc = contract.get("max_chars")
		if mc is not None and int(mc) != int(max_c):
			raise ValueError("manifest l1_contract.max_chars 与当前配置不一致，拒绝 L1B")
		se = contract.get("sentence_endings")
		if isinstance(se, list):
			if set(str(x) for x in se) != set(str(x) for x in sentence_endings):
				raise ValueError("manifest l1_contract.sentence_endings 与当前配置不一致，拒绝 L1B")
		sm = contract.get("segmentation_mode")
		if sm is not None and str(sm) != seg_mode:
			raise ValueError("manifest l1_contract.segmentation_mode 与当前配置不一致，拒绝 L1B")
		if language is None and contract.get("asr_language"):
			language = str(contract["asr_language"])
	if language is None:
		language = str(manifest_data.get("language") or "Chinese")

	if seg_mode != "punctuation":
		raise ValueError("L1A/L1B 解耦路径暂仅支持 segmentation_mode=punctuation")
	if not forced_aligner_path.exists():
		raise FileNotFoundError(f"Forced aligner 目录不存在: {forced_aligner_path}")

	spans = segment_raw_text_only(raw_text, sentence_endings, max_c)
	ann_work = copy.deepcopy(annotations)
	validate_index_text_against_spans(ann_work, spans)

	wav_path = run.output_dir / AUDIO_16K_WAV_NAME
	if not wav_path.is_file():
		raise FileNotFoundError(f"L1B 需要 L1A 产出的缓存音轨: {wav_path}")
	audio_arr, audio_sr = read_audio_16k_wav(wav_path)

	torch_dtype = _torch_dtype(dtype)
	with log_stage(
		"l1b.load_aligner",
		backend=backend,
		device=device,
		forced_aligner_path=str(forced_aligner_path),
	):
		aligner = _build_forced_aligner_only(
			forced_aligner_path=forced_aligner_path,
			device=device,
			dtype=torch_dtype,
			backend=backend,
		)

	with log_stage("l1b.forced_align", language=language):
		align_items = _forced_align_chunked(
			aligner, raw_text, audio_arr, audio_sr, language
		)
	validate_alignment_kept_match(raw_text, align_items)
	validate_align_items_monotonic(align_items)

	timings = assign_times_to_spans(spans, align_items)
	if len(timings) != len(ann_work):
		raise ValueError("句级时间条数与 annotations 不一致")

	video_path = Path(str(manifest_data.get("source") or run.video_path))
	if not video_path.is_file():
		sm = manifest_data.get("source_media")
		if isinstance(sm, dict) and sm.get("path"):
			vp = Path(str(sm["path"]))
			if vp.is_file():
				video_path = vp
	duration = duration_seconds(video_path) if video_path.is_file() else 0.0

	for ann, tm in zip(ann_work, timings):
		if int(ann.get("index", -1)) != tm.index:
			raise ValueError("回填时 index 错位")
		if tm.t_start is None or tm.t_end is None:
			ann["t_start"] = None
			ann["t_end"] = None
			ann["gap_after"] = None
		else:
			ann["t_start"] = float(tm.t_start)
			ann["t_end"] = float(tm.t_end)
	for i, ann in enumerate(ann_work):
		if ann.get("t_start") is None:
			continue
		if i + 1 < len(ann_work):
			nxt = ann_work[i + 1]
			if nxt.get("t_start") is not None:
				ann["gap_after"] = max(0.0, float(nxt["t_start"]) - float(ann["t_end"]))
		elif duration > 0:
			ann["gap_after"] = max(0.0, float(duration) - float(ann["t_end"]))
		else:
			ann["gap_after"] = 0.0

	return compact_annotations(ann_work)


def run_l1b_align_only(
	run: PipelineRun,
	*,
	forced_aligner_path: Path,
	config: AppConfig | None = None,
	backend: str = "transformers",
	device: str = "cuda:0",
	dtype: str = "float16",
	language: str | None = None,
	gpu_memory_utilization: float = 0.8,
	split_pause_threshold: float | None = None,
	silence_threshold: float | None = None,
	max_chars: int | None = None,
	segmentation_mode: str | None = None,
) -> None:
	"""L1B：仅强制对齐，回填 ``t_start``/``t_end``/``gap_after``，不改 index/content。"""
	setup_logging_for_manifest(run.manifest_path, verbose=False)
	if config is None:
		from autosmartcut.config import load_config

		config = load_config()
	validate_manifest_for_l1b(run.manifest_path)
	data = load_manifest(run.manifest_path)

	aligned = compute_l1b_aligned_annotations(
		run,
		data,
		forced_aligner_path=forced_aligner_path,
		config=config,
		backend=backend,
		device=device,
		dtype=dtype,
		language=language,
		gpu_memory_utilization=gpu_memory_utilization,
		split_pause_threshold=split_pause_threshold,
		silence_threshold=silence_threshold,
		max_chars=max_chars,
		segmentation_mode=segmentation_mode,
	)

	data["annotations"] = aligned
	with log_stage("l1b.persist_manifest", manifest=str(run.manifest_path)):
		touch_layer_status(data, "l1b")
		touch_layer_status(data, "l1")
		save_manifest(run.manifest_path, data, atomic=True)

	log_stage_result(
		"l1b.output",
		summary=f"annotations={len(data['annotations'])} manifest={run.manifest_path}",
	)
	logger.info("[L1B] 完成 → %s", run.manifest_path)


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
	"""L1 端到端：``run_l1a_asr_only`` + ``run_l1b_align_only``，写入完整句级时间轴。

	``include_char_timestamps`` 保留为兼容参数；L1A/L1B 路径下不落盘字级时间戳。
	"""
	_ = include_char_timestamps
	if config is None:
		from autosmartcut.config import load_config

		config = load_config()
	_, _, max_c, _, seg_mode = _resolve_perception_params(
		config,
		split_pause_threshold=split_pause_threshold,
		silence_threshold=silence_threshold,
		max_chars=max_chars,
		segmentation_mode=segmentation_mode,
	)
	if not run.video_path.exists():
		raise FileNotFoundError(f"输入视频不存在: {run.video_path}")
	if not asr_model_path.exists():
		raise FileNotFoundError(f"ASR 模型目录不存在: {asr_model_path}")
	if not forced_aligner_path.exists():
		raise FileNotFoundError(f"Forced aligner 目录不存在: {forced_aligner_path}")

	run_l1a_asr_only(
		run,
		asr_model_path=asr_model_path,
		config=config,
		backend=backend,
		device=device,
		dtype=dtype,
		language=language,
		gpu_memory_utilization=gpu_memory_utilization,
		split_pause_threshold=split_pause_threshold,
		silence_threshold=silence_threshold,
		max_chars=max_chars,
		segmentation_mode=segmentation_mode,
	)
	run_l1b_align_only(
		run,
		forced_aligner_path=forced_aligner_path,
		config=config,
		backend=backend,
		device=device,
		dtype=dtype,
		language=language,
		gpu_memory_utilization=gpu_memory_utilization,
		split_pause_threshold=split_pause_threshold,
		silence_threshold=silence_threshold,
		max_chars=max_chars,
		segmentation_mode=segmentation_mode,
	)

	data = load_manifest(run.manifest_path)
	anns = data.get("annotations", [])
	n = len(anns) if isinstance(anns, list) else 0
	log_lazy_json(
		"L1",
		"annotations 完整输出",
		lambda: data.get("annotations", []),
	)
	log_stage_result(
		"l1.output",
		summary=f"segmentation={seg_mode} annotations={n} manifest={run.manifest_path}",
	)
	logger.info("[L1] 完成 segmentation=%s 标注数=%d → %s", seg_mode, n, run.manifest_path)
