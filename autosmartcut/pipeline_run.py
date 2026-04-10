"""单次流水线运行的操作元信息（非 TimelineManifest 内容模型）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ulid import ULID

from autosmartcut.layer2_tokens import video_path_from_tokens_json


def _resolve_source_video(source: str, ref_json: Path) -> Path:
	"""与 execution.resolve_media_path 一致。"""
	p = Path(source)
	if p.is_file():
		return p.resolve()
	cand = ref_json.parent / source
	if cand.is_file():
		return cand.resolve()
	cand = Path.cwd() / source
	if cand.is_file():
		return cand.resolve()
	raise FileNotFoundError(
		f"无法解析源视频: {source!r}（已查 {ref_json.parent} 与当前工作目录）"
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
	"""贯穿 L1→L2→L3 的运行句柄。

	L2 输入为 **JSON2**（``tokens_json_path``）；L3 仍为 **JSON1 + JSON3**。
	"""

	run_id: str
	video_path: Path
	output_dir: Path
	goal: str
	started_at: datetime
	"""Layer1 清单路径（时间轴，供 L3）。"""
	json1_path: Path
	"""智能层输出 keep_mask（JSON3）。"""
	json3_path: Path
	"""智能层输入句面（JSON2）。"""
	tokens_json_path: Path
	output_video_name: str | None = None

	@property
	def json2_path(self) -> Path:
		"""与 ``tokens_json_path`` 同义（历史属性名）。"""
		return self.tokens_json_path

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
		"""全链路：L1 写入 JSON1/JSON2，L2 读 JSON2，L3 读 JSON1+JSON3。"""
		run_id = str(ULID())
		vp = video_path.resolve()
		if output_dir is None:
			od = vp.parent / f"ascut_out_{run_id[:8]}"
		else:
			od = Path(output_dir).resolve()
		od.mkdir(parents=True, exist_ok=True)
		j1 = od / "layer1_annotations.json"
		j2 = od / "layer2_input.json"
		j3 = od / "layer2_output.json"
		return cls(
			run_id=run_id,
			video_path=vp,
			output_dir=od,
			goal=goal,
			started_at=datetime.now(),
			json1_path=j1,
			json3_path=j3,
			tokens_json_path=j2,
			output_video_name=output_video_name,
		)

	@classmethod
	def from_stage2(
		cls,
		layer2_tokens_json: Path,
		goal: str = "",
		output_dir: Path | None = None,
		layer1_json: Path | None = None,
		output_video_name: str | None = None,
	) -> PipelineRun:
		"""从 L2 起：必须已有 JSON2；JSON1 默认与 JSON2 同目录下的 layer1_annotations.json。"""
		run_id = str(ULID())
		t2 = layer2_tokens_json.resolve()
		if not t2.is_file():
			raise FileNotFoundError(f"找不到 JSON2（句面）: {t2}")
		od = Path(output_dir).resolve() if output_dir else t2.parent
		od.mkdir(parents=True, exist_ok=True)
		j1 = Path(layer1_json).resolve() if layer1_json is not None else od / "layer1_annotations.json"
		if not j1.is_file():
			raise FileNotFoundError(
				f"找不到 JSON1（时间轴，L3 需要）: {j1}；请用 --layer1-json 指定或置于输出目录"
			)
		vp = video_path_from_tokens_json(t2)
		j3 = od / "layer2_output.json"
		return cls(
			run_id=run_id,
			video_path=vp,
			output_dir=od,
			goal=goal,
			started_at=datetime.now(),
			json1_path=j1,
			json3_path=j3,
			tokens_json_path=t2,
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
		"""从 L3 起：指定 JSON1 + JSON3。"""
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
		j2 = od / "layer2_input.json"
		return cls(
			run_id=run_id,
			video_path=vp,
			output_dir=od,
			goal="",
			started_at=datetime.now(),
			json1_path=j1,
			json3_path=j3,
			tokens_json_path=j2,
			output_video_name=output_video_name,
		)
