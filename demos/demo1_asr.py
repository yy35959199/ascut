from __future__ import annotations

"""
Demo 1：Qwen3-ASR + ForcedAligner + 句级聚合 + 间隙静音推导

测试指令：
	1. 使用默认配置运行：
	   python demos/demo1_asr.py
	2. 指定输入、输出与模型目录：
	   python demos/demo1_asr.py --input samples/alxe_01.mp4 --output outputs/demo1_annotations.json --asr-model models/Qwen3-ASR-1.7B --forced-aligner models/Qwen3-ForcedAligner-0.6B
	3. 覆盖切分/静音阈值，观察聚合粒度变化：
	   python demos/demo1_asr.py --split-pause-threshold 0.18 --silence-threshold 0.80 --max-chars 50
	4. 指定配置文件运行：
	   python demos/demo1_asr.py --config config.toml

参数说明：
	--config：配置文件路径，默认读取项目根目录的 config.toml。
	--input：输入音视频路径。
	--asr-model：Qwen3-ASR 模型目录。
	--forced-aligner：Qwen3-ForcedAligner 模型目录。
	--output：输出 JSON 路径。
	--language：识别语言，默认 Chinese。
	--backend：推理后端，Windows 建议 transformers。
	--device：transformers 后端设备映射，如 cuda:0。
	--dtype：模型推理精度，支持 float16 / bfloat16 / float32。
	--split-pause-threshold：字级时间戳切分阈值；越小切得越细。
	--silence-threshold：插入 silence annotation 的阈值；应显著大于切分阈值。
	--max-chars：单个聚合片段最大字数兜底。
	--gpu-memory-utilization：vLLM 后端显存利用率。
"""

import argparse
import json
from pathlib import Path

import torch
from qwen_asr.inference.qwen3_asr import Qwen3ASRModel

from autosmartcut.config import load_config
from autosmartcut.perception import (
	build_layer1_document,
	build_layer2_input_document,
	compact_annotations,
	duration_seconds,
	load_audio_mono,
)

def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Demo 1: Qwen3-ASR + ForcedAligner + 句级聚合 + 间隙静音推导"
	)
	parser.add_argument(
		"--config",
		type=Path,
		default=None,
		help="配置文件路径（默认读取项目根目录的 config.toml）",
	)
	parser.add_argument(
		"--input",
		type=Path,
		default=Path("samples/alxe_01.mp4"),
		help="输入音视频文件路径（可为 mp4/wav 等）",
	)
	parser.add_argument(
		"--asr-model",
		type=Path,
		default=Path("models/Qwen3-ASR-1.7B"),
		help="Qwen3-ASR-1.7B 模型目录",
	)
	parser.add_argument(
		"--forced-aligner",
		type=Path,
		default=Path("models/Qwen3-ForcedAligner-0.6B"),
		help="Qwen3-ForcedAligner-0.6B 模型目录",
	)
	parser.add_argument(
		"--output",
		type=Path,
		default=Path("outputs/demo1_annotations.json"),
		help="输出 JSON 路径",
	)
	parser.add_argument(
		"--language",
		type=str,
		default="Chinese",
		help="识别语言（例如 Chinese）",
	)
	parser.add_argument(
		"--backend",
		type=str,
		choices=["transformers", "vllm"],
		default="transformers",
		help="推理后端，Windows 建议 transformers",
	)
	parser.add_argument(
		"--device",
		type=str,
		default="cuda:0",
		help="transformers 后端下 forced aligner 的 device_map",
	)
	parser.add_argument(
		"--dtype",
		type=str,
		choices=["float16", "bfloat16", "float32"],
		default="float16",
		help="模型 dtype",
	)
	parser.add_argument(
		"--split-pause-threshold",
		type=float,
		default=None,
		help="句级切分阈值（秒），仅在 timing 模式下使用（覆盖配置文件）",
	)
	parser.add_argument(
		"--segmentation-mode",
		type=str,
		choices=["punctuation", "timing"],
		default=None,
		help="聚合方式：punctuation（按 raw_text 标点）或 timing（按字级停顿）；默认使用配置文件",
	)
	parser.add_argument(
		"--silence-threshold",
		type=float,
		default=None,
		help="silence annotation 阈值（秒），覆盖配置文件",
	)
	parser.add_argument(
		"--max-chars",
		type=int,
		default=None,
		help="句级聚合最大字数兜底，覆盖配置文件",
	)
	parser.add_argument(
		"--gpu-memory-utilization",
		type=float,
		default=0.8,
		help="vLLM 后端 GPU 显存利用率",
	)
	return parser.parse_args()


def _torch_dtype(dtype_name: str) -> torch.dtype:
	mapping = {
		"float16": torch.float16,
		"bfloat16": torch.bfloat16,
		"float32": torch.float32,
	}
	return mapping[dtype_name]


def _build_model(args: argparse.Namespace) -> Qwen3ASRModel:
	asr_path = str(args.asr_model)
	forced_path = str(args.forced_aligner)
	dtype = _torch_dtype(args.dtype)

	if args.backend == "transformers":
		return Qwen3ASRModel.from_pretrained(
			pretrained_model_name_or_path=asr_path,
			forced_aligner=forced_path,
			forced_aligner_kwargs={
				"dtype": dtype,
				"device_map": args.device,
			},
			dtype=dtype,
			device_map=args.device,
			max_new_tokens=1024,
		)

	return Qwen3ASRModel.LLM(
		model=asr_path,
		forced_aligner=forced_path,
		forced_aligner_kwargs={
			"dtype": dtype,
			"device_map": args.device,
		},
		gpu_memory_utilization=args.gpu_memory_utilization,
		max_new_tokens=1024,
	)


def main() -> None:
	args = parse_args()
	config = load_config(args.config)
	perception_config = config.perception
	split_pause_threshold = (
		args.split_pause_threshold
		if args.split_pause_threshold is not None
		else perception_config.split_pause_threshold
	)
	silence_threshold = (
		args.silence_threshold
		if args.silence_threshold is not None
		else perception_config.silence_threshold
	)
	max_chars = args.max_chars if args.max_chars is not None else perception_config.max_chars
	sentence_endings = set(perception_config.sentence_endings)
	segmentation_mode = (
		args.segmentation_mode
		if args.segmentation_mode is not None
		else getattr(perception_config, "segmentation_mode", "punctuation")
	)

	if not args.input.exists():
		raise FileNotFoundError(f"Input not found: {args.input}")
	if not args.asr_model.exists():
		raise FileNotFoundError(f"ASR model not found: {args.asr_model}")
	if not args.forced_aligner.exists():
		raise FileNotFoundError(f"Forced aligner model not found: {args.forced_aligner}")

	model = _build_model(args)
	print("[demo1] loading audio via PyAV...")
	audio_arr, audio_sr = load_audio_mono(args.input)
	print(f"[demo1] audio loaded: {audio_arr.shape[0]/audio_sr:.1f}s @ {audio_sr}Hz")
	results = model.transcribe(
		audio=(audio_arr, audio_sr),
		language=args.language,
		return_time_stamps=True,
	)
	if not results:
		raise RuntimeError("ASR returned empty results")

	transcription = results[0]
	raw_text = getattr(transcription, "text", "")
	language = getattr(transcription, "language", "")
	duration = duration_seconds(args.input)
	full_doc = build_layer1_document(
		source=str(args.input),
		language=language,
		raw_text=raw_text,
		transcription=transcription,
		duration=duration,
		segmentation_mode=segmentation_mode,
		split_pause_threshold=split_pause_threshold,
		silence_threshold=silence_threshold,
		max_chars=max_chars,
		sentence_endings=sentence_endings,
		include_char_timestamps=True,
	)
	light_doc = {
		"source": full_doc["source"],
		"language": full_doc["language"],
		"raw_text": full_doc["raw_text"],
		"annotations": compact_annotations(full_doc["annotations"]),
	}
	layer2_doc = build_layer2_input_document(light_doc)

	args.output.parent.mkdir(parents=True, exist_ok=True)
	full_output = args.output.with_name(f"{args.output.stem}_full{args.output.suffix}")
	layer2_output = args.output.with_name("demo2_input.json")
	with full_output.open("w", encoding="utf-8") as f:
		json.dump(full_doc, f, ensure_ascii=False, indent=2)
	with args.output.open("w", encoding="utf-8") as f:
		json.dump(light_doc, f, ensure_ascii=False, indent=2)
	with layer2_output.open("w", encoding="utf-8") as f:
		json.dump(layer2_doc, f, ensure_ascii=False, indent=2)

	print(f"[demo1] backend={args.backend}")
	print(f"[demo1] input={args.input}")
	print(f"[demo1] segmentation_mode={segmentation_mode}")
	print(f"[demo1] split_pause_threshold={split_pause_threshold}")
	print(f"[demo1] silence_threshold={silence_threshold}")
	print(f"[demo1] max_chars={max_chars}")
	print(f"[demo1] annotations={len(light_doc['annotations'])}")
	print(f"[demo1] layer2_tokens={len(layer2_doc['tokens'])}")
	print(f"[demo1] full_output={full_output}")
	print(f"[demo1] output={args.output}")
	print(f"[demo1] layer2_input={layer2_output}")


if __name__ == "__main__":
	# vLLM on Windows requires spawn-safe entrypoint.
	main()
