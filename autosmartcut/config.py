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


@dataclass
class AppConfig:
	perception: PerceptionConfig = field(default_factory=PerceptionConfig)
	execution: ExecutionConfig = field(default_factory=ExecutionConfig)


def load_config(path: Path | None = None) -> AppConfig:
	"""加载 config.toml；不存在时返回默认值。"""
	config = AppConfig()
	config_path = path or _DEFAULT_CONFIG_PATH

	if not config_path.exists():
		return config

	with config_path.open("rb") as file:
		raw = tomllib.load(file)

	execution = raw.get("execution", {})
	config.execution = ExecutionConfig(
		gap_after_cap=float(
			execution.get("gap_after_cap", config.execution.gap_after_cap)
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
	return config