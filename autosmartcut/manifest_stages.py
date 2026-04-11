"""--stage / --from-stage 解析与 CLI 交叉校验（MVP-mini）。"""

from __future__ import annotations

import argparse
import warnings
from pathlib import Path

VALID_SPECS = frozenset({"1", "2", "3", "12", "23", "123"})
FROM_STAGE_MAP = {1: "123", 2: "23", 3: "3"}


def parse_stage_spec(spec: str) -> frozenset[int]:
    s = spec.strip()
    if s not in VALID_SPECS:
        raise ValueError(
            f"非法 --stage {spec!r}；允许: {', '.join(sorted(VALID_SPECS, key=len))}"
        )
    return frozenset(int(c) for c in s)


def resolve_stages(args: argparse.Namespace) -> frozenset[int]:
    """--stage 优先；否则 --from-stage 映射；否则默认 123。二者不可同时指定。"""
    raw = getattr(args, "stage", None)
    has_stage = raw is not None and str(raw).strip() != ""
    has_from = getattr(args, "from_stage", None) is not None
    if has_stage and has_from:
        raise ValueError("不可同时使用 --stage 与 --from-stage")
    if has_stage:
        return parse_stage_spec(str(args.stage))
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
        return parse_stage_spec(FROM_STAGE_MAP[fs])
    return frozenset({1, 2, 3})


def validate_cli_args(
    stages: frozenset[int],
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> None:
    """与 --input / --manifest 的交叉约束。"""
    starts_with_1 = 1 in stages
    if starts_with_1:
        if getattr(args, "input", None) is None:
            parser.error("当 --stage 含 1 时必须提供 --input")
        if getattr(args, "manifest", None) is not None:
            parser.error("当 --stage 含 1 时不要使用 --manifest（本次运行将创建清单）")
        for name in ("layer1_json", "layer2_json", "layer3_json"):
            if getattr(args, name, None) is not None:
                parser.error(f"已弃用 --{name.replace('_', '-') }，请使用单一 timeline_manifest.json")
    else:
        if getattr(args, "manifest", None) is None:
            parser.error("当 --stage 不以 1 开头时必须提供 --manifest")
        if getattr(args, "input", None) is not None:
            parser.error("当 --stage 不以 1 开头时不要提供 --input")
        for name in ("layer1_json", "layer2_json", "layer3_json"):
            if getattr(args, name, None) is not None:
                parser.error(f"已弃用 --{name.replace('_', '-') }，请使用 --manifest")

    if 2 in stages and not starts_with_1:
        mp = Path(args.manifest)
        if not mp.is_file():
            parser.error(f"--manifest 不是有效文件: {mp}")

    if 3 in stages and not starts_with_1:
        mp = Path(args.manifest)
        if not mp.is_file():
            parser.error(f"--manifest 不是有效文件: {mp}")
