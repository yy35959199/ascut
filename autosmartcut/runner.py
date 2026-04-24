"""三层流水线编排与 CLI 入口（ascut，MVP-mini）。

入口::

    ascut run [--stage SPEC] [--input|--manifest ...]
    ascut tui [--stage SPEC] [--input|--manifest ...]

``--stage``：``1`` | ``2`` | ``3`` | ``12`` | ``23`` | ``123`` | ``1a`` | ``1b`` | ``1a2`` | ``1b2`` | ``1a23`` | ``1b23``；省略且未指定 ``--from-stage`` 时等价全流程 ``123``。
``--from-stage`` 已弃用，映射为等价 ``--stage``（见 doc/AutoSmartCut-MVP-Mini.md）。

单一持久化文件：``timeline_manifest.json``（见 ``autosmartcut.manifest_io``）。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
import warnings
from pathlib import Path

from autosmartcut.config import load_config
from autosmartcut.log import setup_logging, setup_logging_tui
from autosmartcut.manifest_io import load_manifest, validate_manifest_for_stages
from autosmartcut.manifest_stages import infer_l1_mode, resolve_stages, validate_cli_args
from autosmartcut.pipeline_run import PipelineRun
from autosmartcut.pipeline_session import PipelineSession

logger = logging.getLogger(__name__)


def _add_pipeline_args(p: argparse.ArgumentParser) -> None:
    """向子命令解析器添加流水线公共参数（run 和 tui 共用）。"""
    p.add_argument(
        "--stage",
        type=str,
        default=None,
        metavar="SPEC",
        help="1|2|3|12|23|123|1a|1b|1a2|1b2|1a23|1b23；省略且未指定 --from-stage 时默认全流程 123",
    )
    p.add_argument(
        "--from-stage",
        type=int,
        choices=[1, 2, 3],
        default=None,
        help="已弃用：1→123，2→23，3→3；请改用 --stage",
    )
    p.add_argument(
        "--input",
        type=Path,
        default=None,
        help="输入视频（--stage 含 1 时必填）",
    )
    p.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="timeline_manifest.json（--stage 不以 1 开头时必填）",
    )
    p.add_argument("--goal", type=str, default="", help="智能层目标（L2 使用）")
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="产物目录；含 1 且省略时 <视频父目录>/ascut_out_<ULID 前8位>",
    )
    p.add_argument(
        "--output-name",
        type=str,
        default=None,
        help="输出视频文件名（basename），落在 output_dir",
    )
    p.add_argument(
        "--interactive-2d",
        action="store_true",
        help="启用 2d TUI 人工审阅；默认 auto 跳过",
    )
    p.add_argument(
        "--two-b-mode",
        type=str,
        choices=["single", "block"],
        default=None,
        help="2b：single 或 block；省略时使用 config.toml 中的 two_b_mode",
    )
    p.add_argument("--config", type=Path, default=None, help="config.toml")
    p.add_argument("--asr-model", type=Path, default=None, help="Qwen3-ASR 模型目录")
    p.add_argument("--forced-aligner", type=Path, default=None, help="ForcedAligner 目录")
    p.add_argument(
        "--backend",
        type=str,
        choices=["transformers", "vllm"],
        default=None,
    )
    p.add_argument("--device", type=str, default="cuda:0")
    p.add_argument(
        "--dtype",
        type=str,
        choices=["float16", "bfloat16", "float32"],
        default="float16",
    )
    p.add_argument("--language", type=str, default="Chinese")
    p.add_argument("--gpu-memory-utilization", type=float, default=0.8)
    p.add_argument("--pre-pad", type=float, default=0.15)
    p.add_argument("--post-pad", type=float, default=0.25)
    p.add_argument("--min-duration", type=float, default=1.0)
    p.add_argument(
        "--no-vad-snap",
        action="store_true",
        help="关闭 L3 VAD 切点吸附",
    )
    p.add_argument("--verbose", action="store_true", help="DEBUG 日志")
    # 占位：validate_cli_args 曾检查旧参数
    p.set_defaults(layer1_json=None, layer2_json=None, layer3_json=None)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ascut",
        description="AutoSmartCut：识别层 → 智能层 → 执行层（timeline_manifest.json）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("run", help="单视频流水线（按 --stage 子集执行）")
    _add_pipeline_args(pr)

    pt = sub.add_parser("tui", help="TUI 模式（Textual 交互界面）")
    _add_pipeline_args(pt)

    args = parser.parse_args(argv)

    with warnings.catch_warnings():
        warnings.simplefilter("always", DeprecationWarning)
        try:
            stages = resolve_stages(args)
        except ValueError as e:
            # find the right subparser to call error()
            sp = pr if args.command == "run" else pt
            sp.error(str(e))
    validate_cli_args(stages, args, pr if args.command == "run" else pt)

    setattr(args, "_resolved_stages", stages)
    return args


def _build_run(args: argparse.Namespace) -> PipelineRun:
    stages: frozenset[int] = getattr(args, "_resolved_stages")
    l1_mode = infer_l1_mode(args, stages)
    if 1 in stages or l1_mode in ("both", "a"):
        return PipelineRun.new(
            video_path=args.input,
            goal=args.goal or "",
            output_dir=args.output_dir,
            output_video_name=args.output_name,
        )
    mp = Path(args.manifest).resolve()
    od_arg = Path(args.output_dir).resolve() if args.output_dir else None
    if od_arg is not None and od_arg != mp.parent.resolve():
        return PipelineRun.fork(mp, od_arg, output_video_name=args.output_name)
    return PipelineRun.from_manifest(
        mp,
        goal_override=args.goal if args.goal else None,
        output_dir=args.output_dir,
        output_video_name=args.output_name,
    )


def _validate_prereq_manifest(stages: frozenset[int], manifest_path: Path) -> None:
    data = load_manifest(manifest_path)
    need: frozenset[int] = frozenset()
    if 2 in stages and 1 not in stages:
        need |= {2}
    if 3 in stages and 2 not in stages:
        need |= {3}
    if need:
        validate_manifest_for_stages(need, data)


def _run_pipeline(args: argparse.Namespace, *, use_tui: bool = False) -> int:
    """流水线主逻辑：基于 PipelineSession + DAG 调度。"""
    cfg = load_config(args.config)

    # CLI 参数覆盖 config
    if args.asr_model is not None:
        cfg.models.asr_model_path = args.asr_model
    if args.forced_aligner is not None:
        cfg.models.forced_aligner_path = args.forced_aligner
    if args.backend is not None:
        cfg.models.backend = args.backend
    if args.two_b_mode is not None:
        cfg.intelligence.two_b_mode = args.two_b_mode

    stages: frozenset[int] = getattr(args, "_resolved_stages")

    try:
        run = _build_run(args)
    except (FileNotFoundError, ValueError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    try:
        _validate_prereq_manifest(stages, run.manifest_path)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    if use_tui:
        setup_logging_tui(run, verbose=args.verbose)
    else:
        setup_logging(run, verbose=args.verbose)

    # 将 --stage 映射为 stage_filter
    stage_str = getattr(args, "stage", None)
    if stage_str:
        stage_filter, node_id_filter = PipelineSession.parse_stage_arg(stage_str)
    else:
        stage_filter = frozenset({1, 2, 3})
        node_id_filter = None

    session = PipelineSession(
        manifest_path=run.manifest_path,
        config=cfg,
        stage_filter=stage_filter,
    )
    # 注入细粒度节点过滤（1a/1b 等）
    session._node_id_filter = node_id_filter

    session.register_default_nodes()

    # 选择适配器
    interactive = use_tui or getattr(args, "interactive_2d", False)
    if interactive:
        from autosmartcut.tui_adapter import TUIAdapter
        adapter = TUIAdapter(session)
        try:
            asyncio.run(adapter.start_async())
        except Exception as e:
            logger.exception("TUI 流水线失败: %s", e)
            return 1
    else:
        from autosmartcut.cli_adapter import CLIAdapter
        adapter = CLIAdapter(session)
        try:
            adapter.start_sync()
        except Exception as e:
            logger.exception("流水线失败: %s", e)
            return 1

    return 0


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.command == "run":
        code = _run_pipeline(args, use_tui=False)
        raise SystemExit(code)
    if args.command == "tui":
        code = _run_pipeline(args, use_tui=True)
        raise SystemExit(code)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
