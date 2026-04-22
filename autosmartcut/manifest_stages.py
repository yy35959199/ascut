"""--stage / --from-stage 解析与 CLI 交叉校验（MVP-mini）。"""

from __future__ import annotations

import argparse
import warnings
from pathlib import Path

VALID_SPECS = frozenset(
	{
		"1",
		"2",
		"3",
		"12",
		"23",
		"123",
		"1a",
		"1b",
		"1a2",
		"1b2",
		"1a23",
		"1b23",
	}
)

L1_MODE_BY_SPEC: dict[str, str] = {
	"1": "both",
	"2": "none",
	"3": "none",
	"12": "both",
	"23": "none",
	"123": "both",
	"1a": "a",
	"1b": "b",
	"1a2": "a",
	"1b2": "b",
	"1a23": "a",
	"1b23": "b",
}

FROM_STAGE_MAP = {1: "123", 2: "23", 3: "3"}


def infer_l1_mode(args: argparse.Namespace, stages: frozenset[int]) -> str:
	"""未经过 ``resolve_stages`` 时按 ``stages`` 推断；否则使用 ``args._l1_mode``。"""
	m = getattr(args, "_l1_mode", None)
	if m is not None:
		return str(m)
	return "both" if 1 in stages else "none"


def parse_stage_spec(spec: str) -> frozenset[int]:
	s = spec.strip()
	if s not in VALID_SPECS:
		allowed = ", ".join(sorted(VALID_SPECS, key=len))
		raise ValueError(f"非法 --stage {spec!r}；允许: {allowed}")
	if s in ("1", "12", "123"):
		return frozenset(int(c) for c in s)
	if s == "1a":
		return frozenset({1})
	if s == "1a2":
		return frozenset({1, 2})
	if s == "1a23":
		return frozenset({1, 2, 3})
	if s == "1b":
		return frozenset()
	if s == "1b2":
		return frozenset({2})
	if s == "1b23":
		return frozenset({2, 3})
	if s in ("2", "3", "23"):
		return frozenset(int(c) for c in s)
	raise ValueError(f"非法 --stage {spec!r}")


def resolve_stages(args: argparse.Namespace) -> frozenset[int]:
	"""--stage 优先；否则 --from-stage 映射；否则默认 123。二者不可同时指定。"""
	raw = getattr(args, "stage", None)
	has_stage = raw is not None and str(raw).strip() != ""
	has_from = getattr(args, "from_stage", None) is not None
	if has_stage and has_from:
		raise ValueError("不可同时使用 --stage 与 --from-stage")
	if has_stage:
		s = str(args.stage).strip()
		setattr(args, "_l1_mode", L1_MODE_BY_SPEC[s])
		return parse_stage_spec(s)
	if has_from:
		fs = int(args.from_stage)
		if fs not in FROM_STAGE_MAP:
			raise ValueError(f"非法 --from-stage {fs}")
		warnings.warn(
			"--from-stage 已弃用，请改用 --stage "
			+ repr(FROM_STAGE_MAP[fs]),
			DeprecationWarning,
			stacklevel=2,
		)
		setattr(args, "_l1_mode", "both")
		return parse_stage_spec(FROM_STAGE_MAP[fs])
	setattr(args, "_l1_mode", "both")
	return frozenset({1, 2, 3})


def validate_cli_args(
	stages: frozenset[int],
	args: argparse.Namespace,
	parser: argparse.ArgumentParser,
) -> None:
	"""与 --input / --manifest 的交叉约束。"""
	l1_mode = infer_l1_mode(args, stages)
	needs_video_input = l1_mode in ("both", "a")

	if needs_video_input:
		if getattr(args, "input", None) is None:
			parser.error("当 --stage 为含 L1A/完整 L1 时必须提供 --input")
		if getattr(args, "manifest", None) is not None:
			parser.error("当 --stage 为含 L1A/完整 L1 时不要使用 --manifest（本次运行将创建清单）")
		for name in ("layer1_json", "layer2_json", "layer3_json"):
			if getattr(args, name, None) is not None:
				parser.error(
					f"已弃用 --{name.replace('_', '-') }，请使用单一 timeline_manifest.json"
				)
	else:
		if getattr(args, "manifest", None) is None:
			parser.error("当 --stage 不以 L1A/完整 L1 开头时必须提供 --manifest")
		if getattr(args, "input", None) is not None:
			parser.error("当 --stage 不以 L1A/完整 L1 开头时不要提供 --input")
		for name in ("layer1_json", "layer2_json", "layer3_json"):
			if getattr(args, name, None) is not None:
				parser.error(
					f"已弃用 --{name.replace('_', '-') }，请使用 --manifest"
				)
		mp = Path(args.manifest)
		if not mp.is_file():
			parser.error(f"--manifest 不是有效文件: {mp}")
