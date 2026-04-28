from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
	# L1A 完成后 L1B 与 L2 是否并行（--stage 含 1a* 且含 2；可用 CLI 关闭）
	parallel_l1b_l2_enabled: bool = True


@dataclass
class ModelConfig:
	asr_model_path: Path = Path("models/Qwen3-ASR-1.7B")
	forced_aligner_path: Path = Path("models/Qwen3-ForcedAligner-0.6B")
	# ASR 推理后端："transformers" 或 "vllm"
	# ForcedAligner 始终使用 transformers（NAR 模型，无 vLLM 接口）
	backend: str = "transformers"


@dataclass
class IntelligenceConfig:
	# 2b 模式："single"（全文一次调用）或 "block"（每个 outline 块一次调用）
	two_b_mode: str = "single"
	# 2b block 模式下，单块句数超过此值时记录警告（0 = 不限制/不警告）
	two_b_block_size_limit: int = 0
	# 2c 审核：最大修正轮次（0=占位透传，1=审核+最多1轮修正）
	two_c_max_review_rounds: int = 1
	# 2c 审核：must 项通过率阈值（1.0=全部 must 必须通过）
	two_c_must_pass_rate: float = 1.0
	# 2d 人工审阅：最大回流次数（0=禁用回流，仅手动 toggle + 确认）
	two_d_max_reflows: int = 3


_LLM_STAGE_DEFAULTS: dict[str, Any] = {
	"model": "deepseek-v4-flash",
	"thinking": False,
	"reasoning_effort": "high",
	"temperature": 0.3,
	"max_tokens": 65536,
}


@dataclass(frozen=True)
class LLMStageConfig:
	"""单阶段 LLM 参数（已由 default + stage 合并）。"""

	model: str
	thinking: bool
	reasoning_effort: str
	temperature: float
	max_tokens: int


@dataclass
class LLMConfig:
	"""[llm] 段完整配置。"""

	api_key: str
	base_url: str
	default: LLMStageConfig
	stages: dict[str, LLMStageConfig]

	def for_stage(self, stage: str) -> LLMStageConfig:
		"""未知 stage 使用 default。"""
		return self.stages.get(stage, self.default)


def _coerce_llm_stage(merged: dict[str, Any]) -> LLMStageConfig:
	model = str(merged.get("model", _LLM_STAGE_DEFAULTS["model"]))
	thinking = bool(merged.get("thinking", _LLM_STAGE_DEFAULTS["thinking"]))
	reasoning_effort = str(
		merged.get("reasoning_effort", _LLM_STAGE_DEFAULTS["reasoning_effort"])
	).lower()
	if reasoning_effort not in ("high", "max"):
		raise ValueError(
			f"reasoning_effort 须为 high 或 max，实际: {reasoning_effort!r}"
		)
	temperature = float(merged.get("temperature", _LLM_STAGE_DEFAULTS["temperature"]))
	if not 0.0 <= temperature <= 2.0:
		raise ValueError(f"temperature 须在 0~2 内，实际: {temperature}")
	max_tokens = int(merged.get("max_tokens", _LLM_STAGE_DEFAULTS["max_tokens"]))
	if max_tokens <= 0:
		raise ValueError(f"max_tokens 须为正整数，实际: {max_tokens}")
	return LLMStageConfig(
		model=model,
		thinking=thinking,
		reasoning_effort=reasoning_effort,
		temperature=temperature,
		max_tokens=max_tokens,
	)


@dataclass
class AppConfig:
	perception: PerceptionConfig = field(default_factory=PerceptionConfig)
	execution: ExecutionConfig = field(default_factory=ExecutionConfig)
	models: ModelConfig = field(default_factory=ModelConfig)
	intelligence: IntelligenceConfig = field(default_factory=IntelligenceConfig)
	llm: LLMConfig | None = None


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
		backend=str(models.get("backend", config.models.backend)),
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
		parallel_l1b_l2_enabled=bool(
			execution.get(
				"parallel_l1b_l2_enabled",
				config.execution.parallel_l1b_l2_enabled,
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
		two_b_mode=str(intel.get("two_b_mode", config.intelligence.two_b_mode)),
		two_b_block_size_limit=max(0, limit),
		two_c_max_review_rounds=max(0, int(intel.get(
			"two_c_max_review_rounds",
			config.intelligence.two_c_max_review_rounds,
		))),
		two_c_must_pass_rate=float(intel.get(
			"two_c_must_pass_rate",
			config.intelligence.two_c_must_pass_rate,
		)),
		two_d_max_reflows=max(0, int(intel.get(
			"two_d_max_reflows",
			config.intelligence.two_d_max_reflows,
		))),
	)

	llm_raw = raw.get("llm")
	if isinstance(llm_raw, dict) and llm_raw:
		api_key = str(llm_raw.get("api_key", "")).strip()
		base_url = str(llm_raw.get("base_url", "")).strip()
		if not api_key:
			raise ValueError("config.toml [llm] 缺少 api_key")
		if not base_url:
			raise ValueError("config.toml [llm] 缺少 base_url")

		default_tbl = llm_raw.get("default", {})
		if not isinstance(default_tbl, dict):
			default_tbl = {}
		default_merged = {**_LLM_STAGE_DEFAULTS, **default_tbl}
		default_cfg = _coerce_llm_stage(default_merged)

		stages_tbl = llm_raw.get("stages", {})
		if not isinstance(stages_tbl, dict):
			stages_tbl = {}
		stages: dict[str, LLMStageConfig] = {}
		for name, stage_raw in stages_tbl.items():
			if not isinstance(stage_raw, dict):
				continue
			merged_stage = {**default_merged, **stage_raw}
			stages[str(name)] = _coerce_llm_stage(merged_stage)

		config.llm = LLMConfig(
			api_key=api_key,
			base_url=base_url,
			default=default_cfg,
			stages=stages,
		)

	return config