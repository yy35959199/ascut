"""单次流水线运行的操作元信息（非 TimelineManifest 内容模型）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ulid import ULID


def _resolve_source_video(source: str, layer1_path: Path) -> Path:
	"""与 execution.resolve_media_path 一致：解析 JSON1 中 source 字段为可读视频路径。"""
	p = Path(source)
	if p.is_file():
		return p.resolve()
	cand = layer1_path.parent / source
	if cand.is_file():
		return cand.resolve()
	cand = Path.cwd() / source
	if cand.is_file():
		return cand.resolve()
	raise FileNotFoundError(
		f"无法从 JSON1 解析源视频: {source!r}（已查 layer1 同目录与当前工作目录）"
	)


def _video_path_from_layer1_json(layer1_path: Path) -> Path:
	with layer1_path.open(encoding="utf-8") as f:
		data = json.load(f)
	src = data.get("source")
	if not src:
		raise ValueError(f"JSON1 缺少 source 字段: {layer1_path}")
	return _resolve_source_video(str(src), layer1_path)


@dataclass(frozen=True)
class PipelineRun:
	"""贯穿 L1→L2→L3 的运行句柄；JSON 路径显式存储，可与 output_dir 标准名不一致。"""

	run_id: str
	video_path: Path
	output_dir: Path
	goal: str
	started_at: datetime
	json1_path: Path
	json3_path: Path
	# 仅文件名（含扩展名），写入 output_dir；None 则默认「源 stem + _cut + 后缀」
	output_video_name: str | None = None

	@property
	def json2_path(self) -> Path:
		return self.output_dir / "layer2_input.json"

	@property
	def log_path(self) -> Path:
		return self.output_dir / f"run_{self.run_id}.log"

	@property
	def output_video(self) -> Path:
		if self.output_video_name:
			name = Path(self.output_video_name).name
			if not name or name in (".", ".."):
				raise ValueError(f"无效的输出视频文件名: {self.output_video_name!r}")
			return self.output_dir / name
		stem = self.video_path.stem
		suffix = self.video_path.suffix or ".mp4"
		return self.output_dir / f"{stem}_cut{suffix}"

	@classmethod
	def new(
		cls,
		video_path: Path,
		goal: str = "",
		output_dir: Path | None = None,
		output_video_name: str | None = None,
	) -> PipelineRun:
		"""全链路：L1 将写入 output_dir 下标准 JSON1/JSON2，L2 写入标准 JSON3。"""
		run_id = str(ULID())
		vp = video_path.resolve()
		if output_dir is None:
			od = vp.parent / f"ascut_out_{run_id[:8]}"
		else:
			od = Path(output_dir).resolve()
		od.mkdir(parents=True, exist_ok=True)
		j1 = od / "layer1_annotations.json"
		j3 = od / "layer2_output.json"
		return cls(
			run_id=run_id,
			video_path=vp,
			output_dir=od,
			goal=goal,
			started_at=datetime.now(),
			json1_path=j1,
			json3_path=j3,
			output_video_name=output_video_name,
		)

	@classmethod
	def from_stage2(
		cls,
		layer1_json: Path,
		goal: str = "",
		output_dir: Path | None = None,
		output_video_name: str | None = None,
	) -> PipelineRun:
		"""从 L2 起：读取已有 JSON1；JSON3 写入 output_dir/layer2_output.json。"""
		run_id = str(ULID())
		j1 = layer1_json.resolve()
		if not j1.is_file():
			raise FileNotFoundError(f"找不到 JSON1: {j1}")
		od = Path(output_dir).resolve() if output_dir else j1.parent
		od.mkdir(parents=True, exist_ok=True)
		vp = _video_path_from_layer1_json(j1)
		j3 = od / "layer2_output.json"
		return cls(
			run_id=run_id,
			video_path=vp,
			output_dir=od,
			goal=goal,
			started_at=datetime.now(),
			json1_path=j1,
			json3_path=j3,
			output_video_name=output_video_name,
		)

	@classmethod
	def from_stage3(
		cls,
		layer1_json: Path,
		layer3_json: Path,
		output_dir: Path | None = None,
		output_video_name: str | None = None,
	) -> PipelineRun:
		"""从 L3 起：指定 JSON1 + JSON3（keep_mask）；goal 不使用，置空。"""
		run_id = str(ULID())
		j1 = layer1_json.resolve()
		j3 = layer3_json.resolve()
		if not j1.is_file():
			raise FileNotFoundError(f"找不到 JSON1: {j1}")
		if not j3.is_file():
			raise FileNotFoundError(f"找不到 JSON3: {j3}")
		od = Path(output_dir).resolve() if output_dir else j1.parent
		od.mkdir(parents=True, exist_ok=True)
		vp = _video_path_from_layer1_json(j1)
		return cls(
			run_id=run_id,
			video_path=vp,
			output_dir=od,
			goal="",
			started_at=datetime.now(),
			json1_path=j1,
			json3_path=j3,
			output_video_name=output_video_name,
		)
