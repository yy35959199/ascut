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
class AppConfig:
	perception: PerceptionConfig = field(default_factory=PerceptionConfig)


def load_config(path: Path | None = None) -> AppConfig:
	"""加载 config.toml；不存在时返回默认值。"""
	config = AppConfig()
	config_path = path or _DEFAULT_CONFIG_PATH

	if not config_path.exists():
		return config

	with config_path.open("rb") as file:
		raw = tomllib.load(file)

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
	return config