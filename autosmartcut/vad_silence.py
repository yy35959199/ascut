"""L3：从视频解码 16kHz 单声道波形，Silero VAD → 静音区间；供切点吸附（snap）使用。

不依赖 smartcut；解码逻辑与 perception.load_audio_mono 一致，避免修改 L1 源文件。
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
) -> list[tuple[float, float]]:
	"""解码音轨 → VAD → 静音区间；时间轴以容器 ``video_duration`` 为准（尾静音自然补全）。"""
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
