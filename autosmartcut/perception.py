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
from typing import Any, Callable, Mapping, Sequence

import av
import bisect
import numpy as np
import torch
from qwen_asr.inference.qwen3_asr import Qwen3ASRModel

from autosmartcut.config import AppConfig
from autosmartcut.log import (
	log_lazy_json,
	log_stage,
	log_stage_result,
	setup_logging_for_manifest,
)
from autosmartcut.manifest_io import load_manifest, save_manifest, touch_layer_status
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
	"""纯文本分句结果（L1 分句与对齐共用 SSOT）。"""

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

	# 修正零时长句子：ForcedAligner 量化步长为 80ms，极短发音可能导致 t_start == t_end。
	# 两遍扫描，职责正交：
	#   第一遍：每句独立决策，零时长一律向后扩 _MIN_DUR（不看邻居，可能产生新重叠）
	#   第二遍：消除相邻重叠，把后句 t_start 推到前句 t_end（级联处理连续重叠）
	# 这是 ascut 侧的防御性后处理，不修改 upstream 对齐器行为。
	_MIN_DUR = 0.01
	for i, t in enumerate(timings):
		if t.t_start is None or t.t_end is None:
			continue
		if t.t_end > t.t_start:
			continue
		# t_start == t_end（逆序已在上面 raise 过，此处只剩相等情况）
		new_te = t.t_start + _MIN_DUR
		timings[i] = SentenceTiming(t.index, t.t_start, new_te)
		logging.getLogger(__name__).warning(
			"[L1B] 句 index=%d 零时长，向后扩 %.3fs: t_end %.5f→%.5f",
			t.index, _MIN_DUR, t.t_end, new_te,
		)

	# 第二遍：消除因扩展产生的相邻重叠（级联推后）
	for i in range(len(timings) - 1):
		a = timings[i]
		b = timings[i + 1]
		if a.t_end is None or b.t_start is None or b.t_end is None:
			continue
		if b.t_start < a.t_end:
			new_bs = a.t_end
			new_be = max(b.t_end, a.t_end + _MIN_DUR)
			timings[i + 1] = SentenceTiming(b.index, new_bs, new_be)
			logging.getLogger(__name__).warning(
				"[L1B] 句 index=%d 与前句重叠，t_start %.5f→%.5f t_end %.5f→%.5f",
				b.index, b.t_start, new_bs, b.t_end, new_be,
			)

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


def _sentence_annos_from_spans(spans: Sequence[SentenceSpan]) -> list[dict[str, Any]]:
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


def run_l1_chunked(
	run: PipelineRun,
	*,
	asr_model_path: Path,
	forced_aligner_path: Path,
	config: AppConfig | None = None,
	backend: str = "transformers",
	device: str = "cuda:0",
	dtype: str = "float16",
	language: str = "Chinese",
	gpu_memory_utilization: float = 0.8,
	progress_callback: Callable[..., Any] | None = None,
	split_pause_threshold: float | None = None,
	silence_threshold: float | None = None,
	max_chars: int | None = None,
	segmentation_mode: str | None = None,
	first_chunk_min_sec: float = 3.0,
	first_chunk_max_sec: float = 15.0,
	normal_chunk_target_sec: float = 30.0,
	silence_snap_radius_sec: float = 5.0,
	silence_min_duration_sec: float = 0.2,
) -> None:
	"""L1 合并路径：转码 → VAD → 分块 → 每块 ASR 后立即强制对齐 → 单次分句与时间回填。"""
	import time as _time
	from collections import Counter

	from autosmartcut.pipeline_events import ProgressEvent
	from autosmartcut.progress_utils import SpeedEstimator
	from autosmartcut.vad_silence import plan_chunks as vad_plan_chunks
	from autosmartcut.vad_silence import silero_speech_segments

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
		raise ValueError("当前仅支持 segmentation_mode=punctuation")
	if not run.video_path.exists():
		raise FileNotFoundError(f"输入视频不存在: {run.video_path}")
	if not asr_model_path.exists():
		raise FileNotFoundError(f"ASR 模型目录不存在: {asr_model_path}")
	if not forced_aligner_path.exists():
		raise FileNotFoundError(f"Forced aligner 目录不存在: {forced_aligner_path}")
	if backend == "vllm":
		logger.warning(
			"ForcedAligner 仅通过 transformers 加载；vLLM 后端下对齐仍走 transformers。"
		)

	_NODE = "l1_perception"

	def _emit(phase: str, payload: dict) -> None:
		if progress_callback is not None:
			try:
				progress_callback(ProgressEvent(node_id=_NODE, phase=phase, payload=payload))
			except Exception as _e:
				logger.warning("[L1] progress_callback 异常（忽略）: %s", _e)

	estimator = SpeedEstimator()

	_emit("transcode_start", {})
	t0 = _time.monotonic()
	wav_cache = run.output_dir / AUDIO_16K_WAV_NAME
	extract_audio_16k_wav(run.video_path, wav_cache)
	transcode_sec = _time.monotonic() - t0
	_emit("transcode_done", {"elapsed_sec": transcode_sec})

	audio_arr, audio_sr = read_audio_16k_wav(wav_cache)
	total_audio_sec = audio_arr.shape[0] / audio_sr
	logger.info("[L1] 音频就绪 %.1fs @ %dHz", total_audio_sec, audio_sr)

	_emit("vad_start", {})
	t1 = _time.monotonic()
	speech_segs = silero_speech_segments(
		audio_arr,
		sample_rate=audio_sr,
		threshold=config.execution.vad_threshold,
		min_silence_duration_ms=config.execution.vad_min_silence_ms,
		speech_pad_ms=config.execution.vad_speech_pad_ms,
	)
	vad_sec = _time.monotonic() - t1
	_emit("vad_done", {"elapsed_sec": vad_sec, "speech_segment_count": len(speech_segs)})

	t2 = _time.monotonic()
	chunk_plan = vad_plan_chunks(
		speech_segs,
		total_audio_sec,
		first_chunk_min_sec=first_chunk_min_sec,
		first_chunk_max_sec=first_chunk_max_sec,
		normal_chunk_target_sec=normal_chunk_target_sec,
		silence_snap_radius_sec=silence_snap_radius_sec,
		silence_min_duration_sec=silence_min_duration_sec,
	)
	if not chunk_plan:
		chunk_plan = [{"start_sec": 0.0, "end_sec": total_audio_sec, "chunk_id": 0}]
	plan_sec = _time.monotonic() - t2
	_emit("plan_done", {"total_chunks": len(chunk_plan), "total_audio_sec": total_audio_sec})

	torch_dtype = _torch_dtype(dtype)
	with log_stage("l1.load_model", backend=backend, device=device, dtype=dtype):
		model = _build_qwen3_asr_model(
			asr_model_path=asr_model_path,
			forced_aligner_path=forced_aligner_path,
			backend=backend,
			device=device,
			dtype=torch_dtype,
			gpu_memory_utilization=gpu_memory_utilization,
		)
	aligner = getattr(model, "forced_aligner", None)
	if aligner is None:
		raise RuntimeError("ASR 模型未挂载 forced_aligner，无法执行逐块对齐")

	align_items: list[Any] = []
	chunk_texts: list[str] = []
	chunk_langs: list[str] = []
	chunk_timings: list[dict[str, Any]] = []
	completed_audio_sec = 0.0
	asr_only_sum = 0.0
	align_total_sec = 0.0

	for chunk in chunk_plan:
		chunk_id = int(chunk["chunk_id"])
		start_sample = int(chunk["start_sec"] * audio_sr)
		end_sample = int(chunk["end_sec"] * audio_sr)
		chunk_wav = audio_arr[start_sample:end_sample]
		chunk_audio_sec = float(chunk["end_sec"] - chunk["start_sec"])
		offset_sec = float(chunk["start_sec"])

		_emit("asr_chunk_start", {
			"chunk_id": chunk_id,
			"total_chunks": len(chunk_plan),
			"chunk_audio_sec": chunk_audio_sec,
			"total_audio_sec": total_audio_sec,
			"completed_audio_sec": completed_audio_sec,
		})

		t_asr = _time.monotonic()
		results = model.transcribe(
			audio=(chunk_wav, audio_sr),
			language=language,
			return_time_stamps=False,
		)
		asr_elapsed = _time.monotonic() - t_asr
		asr_only_sum += asr_elapsed

		align_elapsed = 0.0
		if not results:
			logger.warning("[L1] 块 %d ASR 返回空结果，跳过", chunk_id)
			chunk_texts.append("")
			chunk_langs.append(language)
		else:
			transcription = results[0]
			chunk_text = str(getattr(transcription, "text", ""))
			chunk_lang = str(getattr(transcription, "language", "") or language)
			chunk_texts.append(chunk_text)
			chunk_langs.append(chunk_lang)
			ct = chunk_text.strip()
			if ct:
				t_al = _time.monotonic()
				res = aligner.align(
					audio=(chunk_wav, audio_sr),
					text=chunk_text,
					language=language,
				)
				align_elapsed = _time.monotonic() - t_al
				align_total_sec += align_elapsed
				merged = _merge_align_items_with_offset(list(res[0]), offset_sec)
				align_items.extend(merged)

		estimator.record(chunk_id, chunk_audio_sec, asr_elapsed)
		completed_audio_sec += chunk_audio_sec
		chunk_timings.append({
			"chunk_id": chunk_id,
			"audio_sec": chunk_audio_sec,
			"asr_sec": asr_elapsed,
			"align_sec": align_elapsed,
		})

		text_preview = (chunk_texts[-1][:50] + "…") if len(chunk_texts[-1]) > 53 else chunk_texts[-1]
		_emit("asr_chunk_done", {
			"chunk_id": chunk_id,
			"total_chunks": len(chunk_plan),
			"chunk_audio_sec": chunk_audio_sec,
			"chunk_elapsed_sec": asr_elapsed,
			"align_elapsed_sec": align_elapsed,
			"completed_audio_sec": completed_audio_sec,
			"total_audio_sec": total_audio_sec,
			"estimated_speed": estimator.speed,
			"text_preview": text_preview,
			"text_full": chunk_texts[-1],
		})

	t5 = _time.monotonic()
	raw_text = "".join(chunk_texts)
	lang_out = Counter(chunk_langs).most_common(1)[0][0] if chunk_langs else language
	duration = duration_seconds(run.video_path)

	spans = segment_raw_text_only(raw_text, sentence_endings, max_c)
	ann_work = _sentence_annos_from_spans(spans)
	validate_index_text_against_spans(ann_work, spans)

	validate_alignment_kept_match(raw_text, align_items)
	validate_align_items_monotonic(align_items)

	timings = assign_times_to_spans(spans, align_items)
	if len(timings) != len(ann_work):
		raise ValueError("句级时间条数与 annotations 不一致")

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

	postprocess_sec = _time.monotonic() - t5
	_emit("postprocess_done", {
		"elapsed_sec": postprocess_sec,
		"sentence_count": len(ann_work),
		"raw_text_length": len(raw_text),
	})

	light_doc = {
		"source": str(run.video_path),
		"language": lang_out,
		"raw_text": raw_text,
		"annotations": compact_annotations(ann_work),
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
		sm["audio_16k_path"] = AUDIO_16K_WAV_NAME
	with log_stage("l1.persist_manifest", manifest=str(run.manifest_path)):
		touch_layer_status(data, "l1a")
		touch_layer_status(data, "l1b")
		touch_layer_status(data, "l1")
		save_manifest(run.manifest_path, data, atomic=True)

	total_sec = (
		transcode_sec + vad_sec + plan_sec + asr_only_sum + align_total_sec + postprocess_sec
	)
	logger.info(
		"[L1] 耗时报告: 转码=%.2fs VAD=%.2fs 规划=%.3fs ASR=%.2fs 对齐=%.2fs 后处理=%.3fs 总计=%.2fs",
		transcode_sec, vad_sec, plan_sec, asr_only_sum, align_total_sec, postprocess_sec, total_sec,
	)
	timing_parts = " ".join(
		(
			f"{{chunk_id={t['chunk_id']}: audio={t['audio_sec']:.1f}s "
			f"asr={t['asr_sec']:.1f}s align={t['align_sec']:.1f}s "
			f"speed={t['audio_sec']/t['asr_sec']:.2f}x}}"
		)
		if t["asr_sec"] > 0
		else f"{{chunk_id={t['chunk_id']}: audio={t['audio_sec']:.1f}s asr=0s align={t['align_sec']:.1f}s}}"
		for t in chunk_timings
	)
	logger.info("[L1] 各块耗时: %s", timing_parts)
	logger.info(
		"[L1] 完成 annotations=%d → %s",
		len(light_doc["annotations"]),
		run.manifest_path,
	)


def plan_chunks(
	speech_segments: list[dict[str, float]],
	total_duration: float,
	*,
	first_chunk_min_sec: float = 3.0,
	first_chunk_max_sec: float = 15.0,
	normal_chunk_target_sec: float = 30.0,
	silence_snap_radius_sec: float = 5.0,
	silence_min_duration_sec: float = 0.2,
) -> list[dict[str, Any]]:
	"""将音频按 VAD 静音边界规划为渐进式 ASR 切块。（从 vad_silence 重导出）"""
	from autosmartcut.vad_silence import plan_chunks as _plan_chunks
	return _plan_chunks(
		speech_segments,
		total_duration,
		first_chunk_min_sec=first_chunk_min_sec,
		first_chunk_max_sec=first_chunk_max_sec,
		normal_chunk_target_sec=normal_chunk_target_sec,
		silence_snap_radius_sec=silence_snap_radius_sec,
		silence_min_duration_sec=silence_min_duration_sec,
	)


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
	logging.getLogger("transformers.generation.utils").setLevel(logging.ERROR)
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
	"""L1 端到端：``run_l1_chunked``，写入完整句级时间轴。

	``include_char_timestamps`` 保留为兼容参数；当前路径不落盘字级时间戳。
	"""
	_ = include_char_timestamps
	if config is None:
		from autosmartcut.config import load_config

		config = load_config()
	_, _, _max_c, _, seg_mode = _resolve_perception_params(
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

	run_l1_chunked(
		run,
		asr_model_path=asr_model_path,
		forced_aligner_path=forced_aligner_path,
		config=config,
		backend=backend,
		device=device,
		dtype=dtype,
		language=language,
		gpu_memory_utilization=gpu_memory_utilization,
		progress_callback=None,
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
