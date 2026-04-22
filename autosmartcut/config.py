from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"


@dataclass
class PerceptionConfig:
	# 主切分模式："punctuation" | "timing"
	segmentation_mode: str = "punctuation"
	# timing 模式保留该阈值以向后兼容（仅在 timing 模式下使用）
	split_pause_threshold: float = 0.20
	silence_threshold: float = 0.80
	max_chars: int = 60
	# 默认的标点集合（包含常见中文/英文全/半角标点，作为 punctuation 模式的切分依据）
	sentence_endings: list[str] = field(
		default_factory=lambda: [
			"。", "！", "？", "；", "\n",
			"，", ",", ".", "!", "?", ";", ":", "：",
			"—", "…", "、", "（", "）", "(", ")",
			"“", "”", '"', "'", "《", "》",
		]
	)


@dataclass
class ExecutionConfig:
	# 保留段右边界：最后一句 t_end 后再纳入 min(gap_after, 本值) 秒；0 表示不延伸
	gap_after_cap: float = 0.6
	# L3 切点吸附（Silero VAD）：CLI --no-vad-snap 时忽略以下项
	vad_snap_enabled: bool = True
	vad_snap_radius: float = 0.12
	vad_threshold: float = 0.35
	vad_min_silence_ms: int = 80
	vad_speech_pad_ms: int = 10


@dataclass
class ModelConfig:
	asr_model_path: Path = Path("models/Qwen3-ASR-1.7B")
	forced_aligner_path: Path = Path("models/Qwen3-ForcedAligner-0.6B")


@dataclass
class IntelligenceConfig:
	# 2b chunked：单 outline 块超过该句数时二次拆分子块
	two_b_block_size_limit: int = 50


@dataclass
class AppConfig:
	perception: PerceptionConfig = field(default_factory=PerceptionConfig)
	execution: ExecutionConfig = field(default_factory=ExecutionConfig)
	models: ModelConfig = field(default_factory=ModelConfig)
	intelligence: IntelligenceConfig = field(default_factory=IntelligenceConfig)


def load_config(path: Path | None = None) -> AppConfig:
	"""加载 config.toml；不存在时返回默认值。"""
	config = AppConfig()
	config_path = path or _DEFAULT_CONFIG_PATH

	if not config_path.exists():
		return config

	with config_path.open("rb") as file:
		raw = tomllib.load(file)

	models = raw.get("models", {})
	config.models = ModelConfig(
		asr_model_path=Path(
			models.get("asr_model_path", str(config.models.asr_model_path))
		),
		forced_aligner_path=Path(
			models.get(
				"forced_aligner_path", str(config.models.forced_aligner_path)
			)
		),
	)

	execution = raw.get("execution", {})
	config.execution = ExecutionConfig(
		gap_after_cap=float(
			execution.get("gap_after_cap", config.execution.gap_after_cap)
		),
		vad_snap_enabled=bool(
			execution.get("vad_snap_enabled", config.execution.vad_snap_enabled)
		),
		vad_snap_radius=float(
			execution.get("vad_snap_radius", config.execution.vad_snap_radius)
		),
		vad_threshold=float(
			execution.get("vad_threshold", config.execution.vad_threshold)
		),
		vad_min_silence_ms=int(
			execution.get(
				"vad_min_silence_ms", config.execution.vad_min_silence_ms
			)
		),
		vad_speech_pad_ms=int(
			execution.get(
				"vad_speech_pad_ms", config.execution.vad_speech_pad_ms
			)
		),
	)

	perception = raw.get("perception", {})
	config.perception = PerceptionConfig(
		segmentation_mode=perception.get(
			"segmentation_mode", config.perception.segmentation_mode
		),
		split_pause_threshold=perception.get(
			"split_pause_threshold", config.perception.split_pause_threshold
		),
		silence_threshold=perception.get(
			"silence_threshold", config.perception.silence_threshold
		),
		max_chars=perception.get("max_chars", config.perception.max_chars),
		sentence_endings=perception.get(
			"sentence_endings", config.perception.sentence_endings
		),
	)

	intel = raw.get("intelligence", {})
	limit_raw = intel.get(
		"two_b_block_size_limit", config.intelligence.two_b_block_size_limit
	)
	try:
		limit = int(limit_raw)
	except (TypeError, ValueError):
		limit = config.intelligence.two_b_block_size_limit
	config.intelligence = IntelligenceConfig(
		two_b_block_size_limit=max(1, limit),
	)
	return config