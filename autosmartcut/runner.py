"""三层流水线编排与 CLI 入口（ascut，MVP-mini）。

入口::

    ascut run [--stage SPEC] [--input|--manifest ...]
    ascut tui [--stage SPEC] [--input|--manifest ...]
    ascut resume <path> [--goal ...] [--stage ...] [--tui] [-y]

``--stage``：``1`` | ``2`` | ``3`` | ``12`` | ``23`` | ``123`` | ``1a`` | ``1b`` | ``1a2`` | ``1b2`` | ``1a23`` | ``1b23``；省略且未指定 ``--from-stage`` 时等价全流程 ``123``。
``--from-stage`` 已弃用，映射为等价 ``--stage``（见 doc/AutoSmartCut-MVP-Mini.md）。

``ascut resume``：读取已有 timeline_manifest.json（或其父文件夹），自动推断进度，
展示各层完成状态，并建议下一步 ``--stage``，确认后续跑。

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


def _add_resume_args(p: argparse.ArgumentParser) -> None:
    """向 resume 子命令解析器添加参数。"""
    p.add_argument(
        "path",
        type=Path,
        help="timeline_manifest.json 或其父文件夹",
    )
    p.add_argument(
        "--goal",
        type=str,
        default=None,
        help="覆盖或补充清单中的剪辑意图（续跑 L2 时若清单无 goal 则必填）",
    )
    p.add_argument(
        "--stage",
        type=str,
        default=None,
        metavar="SPEC",
        help="覆盖自动推断的 stage（1|2|3|12|23|123|1a|1b|...）",
    )
    p.add_argument(
        "--tui",
        action="store_true",
        help="使用 TUI 模式执行",
    )
    p.add_argument(
        "--yes", "-y",
        action="store_true",
        help="跳过确认直接执行",
    )
    p.add_argument("--config", type=Path, default=None, help="config.toml")
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="产物目录（默认与清单同目录）",
    )
    p.add_argument(
        "--output-name",
        type=str,
        default=None,
        help="输出视频文件名（basename）",
    )
    p.add_argument("--pre-pad", type=float, default=0.15)
    p.add_argument("--post-pad", type=float, default=0.25)
    p.add_argument(
        "--no-vad-snap",
        action="store_true",
        help="关闭 L3 VAD 切点吸附",
    )
    p.add_argument("--verbose", action="store_true", help="DEBUG 日志")


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

    ps = sub.add_parser(
        "resume",
        help="从已有清单识别进度并选择继续执行",
    )
    _add_resume_args(ps)

    args = parser.parse_args(argv)

    # resume 子命令不走 resolve_stages / validate_cli_args
    if args.command == "resume":
        return args

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


def _print_progress_report(report: "ProgressReport") -> None:
    """CLI 文本格式打印进度报告。"""
    print(f"清单: {report.manifest_path}")
    print(f"  run_id : {report.run_id}")
    print(f"  目标   : {report.goal or '(未设置)'}")
    print(f"  视频   : {report.source_video}")
    if report.duration:
        m, s = divmod(int(report.duration), 60)
        print(f"  时长   : {m}分{s}秒")
    print()
    print("进度状态:")
    for n in report.nodes:
        icon = "✓" if n.completed else "✗"
        at = f"  {n.completed_at[:19]}" if n.completed_at else ""
        print(f"  {icon} {n.display_name:<20}{at}  {n.summary}")
    if report.warnings:
        print()
        for w in report.warnings:
            print(f"  ⚠ {w}")
    if report.suggested_stage:
        print(f"\n建议继续: --stage {report.suggested_stage}")


def _run_resume(args: argparse.Namespace) -> int:
    """resume 子命令主逻辑：推断进度 → 展示 → 确认 → 续跑。"""
    from autosmartcut.manifest_progress import (
        ProgressReport,
        infer_progress,
        resolve_manifest_path,
    )

    # 1. 解析路径
    try:
        mp = resolve_manifest_path(args.path)
    except FileNotFoundError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    # 2. 加载清单 + 推断进度
    try:
        data = load_manifest(mp)
    except Exception as e:
        print(f"错误: 无法读取清单: {e}", file=sys.stderr)
        return 1

    report: ProgressReport = infer_progress(data, mp)

    # 3. 打印进度摘要
    _print_progress_report(report)

    # 4. 全部完成
    if report.all_completed:
        print("\n所有阶段已完成，无需续跑。")
        print(f"如需重跑 L3: ascut run --manifest \"{mp}\" --stage 3")
        return 0

    # 5. 空骨架（无 annotations）
    if report.suggested_stage is None:
        print("\n清单为空骨架，请使用 ascut run --input <视频> 从头开始。")
        return 1

    # 6. 确定 stage（用户覆盖 > 自动推断）
    stage = args.stage or report.suggested_stage

    # 7. goal 检查
    goal = args.goal or report.goal
    if report.goal_needed and not goal:
        print(
            "\n续跑 L2 需要 --goal 参数，请提供剪辑意图。",
            file=sys.stderr,
        )
        print(f"示例: ascut resume \"{args.path}\" --goal \"保留核心内容\"")
        return 1

    # 8. 确认
    cmd_preview = f"ascut run --manifest \"{mp}\" --stage {stage}"
    if goal:
        cmd_preview += f' --goal "{goal}"'
    if args.tui:
        cmd_preview = cmd_preview.replace("ascut run", "ascut tui", 1)

    if not args.yes:
        print(f"\n将执行: {cmd_preview}")
        try:
            answer = input("继续？[Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            return 0
        if answer and answer != "y":
            print("已取消。")
            return 0

    # 9. 构造等价 run/tui 参数并递归调用 main()
    run_argv: list[str] = ["tui" if args.tui else "run"]
    run_argv += ["--manifest", str(mp)]
    run_argv += ["--stage", stage]
    if goal:
        run_argv += ["--goal", goal]
    if args.output_dir:
        run_argv += ["--output-dir", str(args.output_dir)]
    if args.output_name:
        run_argv += ["--output-name", args.output_name]
    if args.config:
        run_argv += ["--config", str(args.config)]
    if getattr(args, "no_vad_snap", False):
        run_argv += ["--no-vad-snap"]
    if args.verbose:
        run_argv += ["--verbose"]

    try:
        main(run_argv)
    except SystemExit as e:
        return int(e.code) if e.code is not None else 0
    return 0


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.command == "run":
        code = _run_pipeline(args, use_tui=False)
        raise SystemExit(code)
    if args.command == "tui":
        code = _run_pipeline(args, use_tui=True)
        raise SystemExit(code)
    if args.command == "resume":
        code = _run_resume(args)
        raise SystemExit(code)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
