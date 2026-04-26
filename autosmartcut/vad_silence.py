"""L3：16kHz 单声道波形 → Silero VAD → 静音区间；供切点吸附（snap）使用。

优先读取清单 ``source_media.audio_16k_path`` 指向的 L1 缓存 WAV（与
:func:`perception.extract_audio_16k_wav` 一致）；缺失时再 PyAV 解码。
不依赖 smartcut。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from autosmartcut.config import ExecutionConfig

_VAD_SAMPLE_RATE = 16000


def decode_audio_mono_16k_for_vad(path: Path, sample_rate: int = _VAD_SAMPLE_RATE) -> np.ndarray:
	"""从媒体文件解码为 mono float32 PCM，强制重采样到 sample_rate（默认 16k）。"""
	import av

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
		return np.zeros(0, dtype=np.float32)
	return np.concatenate(chunks).astype(np.float32)


def speech_segments_to_silence_intervals(
	speech: list[dict[str, float]],
	duration: float,
) -> list[tuple[float, float]]:
	"""语音段列表（秒）→ 静音区间补集 [0, duration]。"""
	if duration <= 0:
		return []
	if not speech:
		return [(0.0, duration)]

	ordered = sorted(speech, key=lambda x: float(x["start"]))
	silences: list[tuple[float, float]] = []
	prev = 0.0
	for seg in ordered:
		s = float(seg["start"])
		e = float(seg["end"])
		if s > prev:
			silences.append((prev, s))
		prev = max(prev, e)
	if prev < duration:
		silences.append((prev, duration))
	return silences


def _snap_out_point(
	b: float,
	silences: list[tuple[float, float]],
	lo: float,
	hi: float,
) -> float | None:
	"""出点：吸附到窗口内「静音段左沿」（刚停说进入静音）。返回 None 表示未命中。"""
	best: float | None = None
	best_dist = float("inf")
	for s, e in silences:
		ol = max(s, lo)
		oe = min(e, hi)
		if ol >= oe:
			continue
		cand = max(s, lo)
		if cand >= oe:
			continue
		dist = abs(cand - b)
		if dist < best_dist:
			best_dist = dist
			best = cand
	return best


def _snap_in_point(
	a: float,
	silences: list[tuple[float, float]],
	lo: float,
	hi: float,
) -> float | None:
	"""入点：吸附到窗口内「静音段右沿」（静音结束将开说）。返回 None 表示未命中。"""
	best: float | None = None
	best_dist = float("inf")
	for s, e in silences:
		ol = max(s, lo)
		oe = min(e, hi)
		if ol >= oe:
			continue
		cand = min(e, hi)
		if cand <= ol:
			continue
		dist = abs(cand - a)
		if dist < best_dist:
			best_dist = dist
			best = cand
	return best


def snap_interval_edges_to_silence(
	intervals: list[tuple[float, float]],
	silences: list[tuple[float, float]],
	*,
	duration: float,
	radius: float,
) -> list[tuple[float, float]]:
	"""对每条保留段左右边界在 ±radius 内吸附到静音；单边失败保持原边；非法则整段回滚。"""
	if radius <= 0 or not silences:
		return intervals
	out: list[tuple[float, float]] = []
	for a, b in intervals:
		a0, b0 = a, b
		lo_a = max(0.0, a - radius)
		hi_a = min(duration, a + radius)
		lo_b = max(0.0, b - radius)
		hi_b = min(duration, b + radius)
		ca = _snap_in_point(a, silences, lo_a, hi_a)
		cb = _snap_out_point(b, silences, lo_b, hi_b)
		a1 = ca if ca is not None else a
		b1 = cb if cb is not None else b
		a1 = max(0.0, min(a1, duration))
		b1 = max(0.0, min(b1, duration))
		if a1 >= b1:
			out.append((a0, b0))
		else:
			out.append((a1, b1))
	return out


def silero_speech_segments(
	wav_1d: np.ndarray,
	*,
	sample_rate: int = _VAD_SAMPLE_RATE,
	threshold: float = 0.35,
	min_silence_duration_ms: int = 80,
	speech_pad_ms: int = 10,
) -> list[dict[str, float]]:
	"""运行 Silero VAD，返回 speech 段列表（秒）。"""
	import torch
	from silero_vad import get_speech_timestamps, load_silero_vad

	if wav_1d.size == 0:
		return []
	model = load_silero_vad(onnx=True)
	wav_t = torch.from_numpy(wav_1d.astype(np.float32))
	if wav_t.dim() > 1:
		wav_t = wav_t.flatten()
	segs = get_speech_timestamps(
		wav_t,
		model,
		sampling_rate=sample_rate,
		threshold=threshold,
		min_silence_duration_ms=min_silence_duration_ms,
		speech_pad_ms=speech_pad_ms,
		return_seconds=True,
		time_resolution=4,
	)
	return [{"start": float(s["start"]), "end": float(s["end"])} for s in segs]


def silence_intervals_for_video(
	video_path: Path,
	video_duration: float,
	execution_cfg: ExecutionConfig,
	*,
	audio_16k_path: Path | None = None,
) -> list[tuple[float, float]]:
	"""解码音轨（或复用 L1 缓存 WAV）→ VAD → 静音区间；时间轴以 ``video_duration`` 为准。"""
	if audio_16k_path is not None and audio_16k_path.is_file():
		import soundfile as sf

		wav, _sr = sf.read(str(audio_16k_path), dtype="float32", always_2d=False)
		wav = np.asarray(wav, dtype=np.float32)
		if wav.ndim > 1:
			wav = np.mean(wav, axis=-1).astype(np.float32)
	else:
		wav = decode_audio_mono_16k_for_vad(video_path, _VAD_SAMPLE_RATE)
	timeline_end = float(video_duration)
	if timeline_end <= 0:
		return []

	speech = silero_speech_segments(
		wav,
		sample_rate=_VAD_SAMPLE_RATE,
		threshold=execution_cfg.vad_threshold,
		min_silence_duration_ms=execution_cfg.vad_min_silence_ms,
		speech_pad_ms=execution_cfg.vad_speech_pad_ms,
	)
	clipped: list[dict[str, float]] = []
	for seg in speech:
		s = max(0.0, float(seg["start"]))
		e = max(0.0, float(seg["end"]))
		e = min(e, timeline_end)
		if e > s:
			clipped.append({"start": s, "end": e})

	return speech_segments_to_silence_intervals(clipped, timeline_end)


# ---------------------------------------------------------------------------
# plan_chunks — VAD 静音边界切块规划（纯函数，无 GPU 依赖）
# ---------------------------------------------------------------------------

def plan_chunks(
	speech_segments: list[dict[str, float]],
	total_duration: float,
	*,
	first_chunk_min_sec: float = 3.0,
	first_chunk_max_sec: float = 15.0,
	normal_chunk_target_sec: float = 30.0,
	silence_snap_radius_sec: float = 5.0,
	silence_min_duration_sec: float = 0.2,
) -> list[dict]:
	"""将音频按 VAD 静音边界规划为渐进式 ASR 切块。

	Args:
		speech_segments: Silero VAD 返回的语音段列表，每条 {"start": float, "end": float}（秒）
		total_duration: 音频总时长（秒）
		first_chunk_min_sec: 第一块最短时长（秒）
		first_chunk_max_sec: 第一块最长时长（秒）
		normal_chunk_target_sec: 后续块目标时长（秒）
		silence_snap_radius_sec: 静音吸附搜索半径（秒）
		silence_min_duration_sec: 有效静音间隔最小时长（秒）

	Returns:
		切块列表，每条 {"start_sec": float, "end_sec": float, "chunk_id": int}

	Postconditions:
		- 切块连续：chunks[i].end_sec == chunks[i+1].start_sec
		- 覆盖全程：chunks[0].start_sec == 0.0，chunks[-1].end_sec == total_duration
		- 无空块：每块 end_sec > start_sec
		- chunk_id 从 0 开始连续递增
	"""
	if total_duration <= 0:
		return []

	# 从语音段派生静音间隔
	silence_gaps: list[tuple[float, float]] = []
	if speech_segments:
		ordered = sorted(speech_segments, key=lambda s: float(s["start"]))
		prev = 0.0
		for seg in ordered:
			s = float(seg["start"])
			e = float(seg["end"])
			if s > prev:
				silence_gaps.append((prev, s))
			prev = max(prev, e)
		if prev < total_duration:
			silence_gaps.append((prev, total_duration))
	else:
		silence_gaps = [(0.0, total_duration)]

	# 过滤满足最小时长的静音间隔
	qualifying = [(s, e) for s, e in silence_gaps if (e - s) >= silence_min_duration_sec]

	chunks: list[dict] = []
	cursor = 0.0
	chunk_id = 0

	# ── 块 0：第一句启发式 ──────────────────────────────────────────────────
	found_boundary: float | None = None
	for gap_s, gap_e in qualifying:
		# 找到 end 落在 [first_chunk_min_sec, first_chunk_max_sec] 内的第一个静音间隔
		if gap_e >= first_chunk_min_sec and gap_e <= first_chunk_max_sec:
			found_boundary = gap_e
			break

	if found_boundary is None:
		found_boundary = min(first_chunk_max_sec, total_duration)

	chunks.append({"start_sec": 0.0, "end_sec": found_boundary, "chunk_id": 0})
	cursor = found_boundary
	chunk_id = 1

	# ── 块 1+：目标时长 + 静音吸附 ──────────────────────────────────────────
	while cursor < total_duration:
		remaining = total_duration - cursor

		# 尾部合并：剩余音频 < 目标时长的 50%，合入上一块
		if remaining < normal_chunk_target_sec * 0.5:
			if chunks:
				chunks[-1]["end_sec"] = total_duration
			else:
				chunks.append({"start_sec": 0.0, "end_sec": total_duration, "chunk_id": 0})
			break

		target = min(cursor + normal_chunk_target_sec, total_duration)

		# 在 ±silence_snap_radius_sec 内搜索最近的静音间隔起点
		snap_lo = target - silence_snap_radius_sec
		snap_hi = target + silence_snap_radius_sec
		best_snap: float | None = None
		best_dist = float("inf")

		for gap_s, gap_e in qualifying:
			cand = gap_s
			if cand >= snap_lo and cand <= snap_hi and cand > cursor:
				dist = abs(cand - target)
				if dist < best_dist:
					best_dist = dist
					best_snap = cand

		end_sec = best_snap if best_snap is not None else target

		# 确保不超过总时长且不产生空块
		end_sec = min(end_sec, total_duration)
		if end_sec <= cursor:
			end_sec = min(cursor + normal_chunk_target_sec, total_duration)

		chunks.append({"start_sec": cursor, "end_sec": end_sec, "chunk_id": chunk_id})
		cursor = end_sec
		chunk_id += 1

	return chunks
