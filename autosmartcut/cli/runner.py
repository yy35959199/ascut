"""三层流水线编排与 CLI 入口（ascut，MVP-mini）。

入口::

    ascut run [--stage SPEC] [--input|--manifest ...]
    ascut tui [--stage SPEC] [--input|--manifest ...]
    ascut resume <path> [--goal ...] [--stage ...] [--tui] [-y]

``--stage``：``1`` | ``2`` | ``3`` | ``12`` | ``23`` | ``123``；省略且未指定 ``--from-stage`` 时等价全流程 ``123``。
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
from autosmartcut.log import setup_logging, setup_logging_tui, setup_logging_tui_for_manifest
from autosmartcut.manifest.manifest_io import load_manifest
from autosmartcut.pipeline.pipeline_session import PipelineSession
from autosmartcut.pipeline.session_factory import PipelineParams

logger = logging.getLogger(__name__)


def _add_tui_args(p: argparse.ArgumentParser) -> None:
    """向 tui 子命令解析器添加参数。

    支持两种用法：
    1. ascut tui <path>  — 智能识别媒体文件或清单文件（推荐）
    2. ascut tui --input video.mp4 --stage 123  — 旧用法（兼容）
    """
    p.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=None,
        help="输入路径：媒体文件（新建工程）或 timeline_manifest.json / 其父文件夹（续跑）",
    )
    # 保留旧参数以兼容
    _add_pipeline_args(p)


def _add_pipeline_args(p: argparse.ArgumentParser) -> None:
    """向子命令解析器添加流水线公共参数（run 使用）。"""
    p.add_argument(
        "--stage",
        type=str,
        default=None,
        metavar="SPEC",
        help="1|2|3|12|23|123；省略且未指定 --from-stage 时默认全流程 123",
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
        help="产物目录；含 1 且省略时 <视频父目录>/ascut_out_<YYYY-mm-DD_HH-MM-ss.SSS>（冲突 _01）",
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
    p.add_argument(
        "--force-rerun",
        type=str,
        default=None,
        metavar="PHASES",
        help="强制重跑指定 phase（如 2、23），忽略已完成的 resumable 节点；续跑时使用",
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
    p.add_argument(
        "--force-rerun",
        type=str,
        default=None,
        metavar="PHASES",
        help="强制重跑指定 phase（如 2、23），忽略已完成的 resumable 节点",
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
    _add_tui_args(pt)

    ps = sub.add_parser(
        "resume",
        help="从已有清单识别进度并选择继续执行",
    )
    _add_resume_args(ps)

    args = parser.parse_args(argv)

    # resume 子命令不走 stage 解析
    if args.command == "resume":
        return args

    # tui 子命令：如果提供了 path 参数，跳过 stage 解析（由 AppController.open() 处理）
    if args.command == "tui" and getattr(args, "path", None) is not None:
        return args

    # ── stage / from-stage 解析（原 manifest_stages.resolve_stages）──────────
    with warnings.catch_warnings():
        warnings.simplefilter("always", DeprecationWarning)
        try:
            stages, l1_mode = _resolve_stages(args)
        except ValueError as e:
            sp = pr if args.command == "run" else pt
            sp.error(str(e))

    # ── CLI 参数交叉校验（原 manifest_stages.validate_cli_args）──────────────
    _validate_cli_args(stages, l1_mode, args, pr if args.command == "run" else pt)

    setattr(args, "_resolved_stages", stages)
    setattr(args, "_l1_mode", l1_mode)
    return args


# ---------------------------------------------------------------------------
# stage 解析与 CLI 校验（原 manifest_stages.py 的逻辑，内联至此）
# ---------------------------------------------------------------------------

_VALID_STAGE_SPECS = frozenset({
    "1", "2", "3", "12", "23", "123",
})

_L1_MODE_BY_SPEC: dict[str, str] = {
    "1": "both",
    "2": "none",
    "3": "none",
    "12": "both",
    "23": "none",
    "123": "both",
}

_FROM_STAGE_MAP = {1: "123", 2: "23", 3: "3"}


def _resolve_stages(args: argparse.Namespace) -> tuple[frozenset[int], str]:
    """解析 --stage / --from-stage，返回 (stage_filter, l1_mode)。"""
    raw = getattr(args, "stage", None)
    has_stage = raw is not None and str(raw).strip() != ""
    has_from = getattr(args, "from_stage", None) is not None

    if has_stage and has_from:
        raise ValueError("不可同时使用 --stage 与 --from-stage")

    if has_stage:
        s = str(args.stage).strip()
        if s not in _VALID_STAGE_SPECS:
            allowed = ", ".join(sorted(_VALID_STAGE_SPECS, key=len))
            raise ValueError(f"非法 --stage {s!r}；允许: {allowed}")
        stage_filter, _ = PipelineSession.parse_stage_arg(s)
        return stage_filter, _L1_MODE_BY_SPEC[s]

    if has_from:
        fs = int(args.from_stage)
        if fs not in _FROM_STAGE_MAP:
            raise ValueError(f"非法 --from-stage {fs}")
        warnings.warn(
            "--from-stage 已弃用，请改用 --stage " + repr(_FROM_STAGE_MAP[fs]),
            DeprecationWarning,
            stacklevel=4,
        )
        s = _FROM_STAGE_MAP[fs]
        stage_filter, _ = PipelineSession.parse_stage_arg(s)
        return stage_filter, "both"

    # 默认全流程
    stage_filter, _ = PipelineSession.parse_stage_arg("123")
    return stage_filter, "both"


def _validate_cli_args(
    stages: frozenset[int],
    l1_mode: str,
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> None:
    """CLI 参数交叉校验（--input / --manifest 约束）。"""
    needs_video_input = l1_mode == "both"

    if needs_video_input:
        if getattr(args, "input", None) is None:
            parser.error("当 --stage 含阶段 1（L1）时必须提供 --input")
        if getattr(args, "manifest", None) is not None:
            parser.error("当 --stage 含阶段 1 时不要使用 --manifest（本次运行将创建清单）")
    else:
        if getattr(args, "manifest", None) is None:
            parser.error("当 --stage 不含阶段 1 时必须提供 --manifest")
        if getattr(args, "input", None) is not None:
            parser.error("当 --stage 不含阶段 1 时不要提供 --input")
        mp = Path(args.manifest)
        if not mp.is_file():
            parser.error(f"--manifest 不是有效文件: {mp}")


def _parse_force_rerun(raw: str | None) -> "frozenset[int] | None":
    """将 --force-rerun 字符串（如 "2"、"23"）解析为 frozenset[int]。

    Returns:
        frozenset of phase ints，或 None（未指定时）。

    Raises:
        ValueError: 包含非法字符。
    """
    if not raw:
        return None
    phases: set[int] = set()
    for ch in raw.strip():
        if ch not in ("1", "2", "3"):
            raise ValueError(f"--force-rerun 只接受 1/2/3 的组合，非法字符: {ch!r}")
        phases.add(int(ch))
    return frozenset(phases) if phases else None


def _args_to_params(args: argparse.Namespace) -> PipelineParams:
    """将 argparse Namespace 转换为 PipelineParams。"""
    stages: frozenset[int] = getattr(args, "_resolved_stages", frozenset({1, 2, 3}))
    l1_mode: str = getattr(args, "_l1_mode", "both")

    input_video = None
    manifest_path = None
    if l1_mode == "both":
        input_video = getattr(args, "input", None)
    else:
        manifest_path = getattr(args, "manifest", None)

    stage_str = getattr(args, "stage", None) or "123"

    force_rerun_raw = getattr(args, "force_rerun", None)
    try:
        force_rerun_phases = _parse_force_rerun(force_rerun_raw)
    except ValueError as e:
        raise ValueError(str(e)) from e

    return PipelineParams(
        input_video=input_video,
        manifest_path=manifest_path,
        stage=stage_str,
        goal=getattr(args, "goal", "") or "",
        output_dir=getattr(args, "output_dir", None),
        output_name=getattr(args, "output_name", None),
        config_path=getattr(args, "config", None),
        asr_model=getattr(args, "asr_model", None),
        forced_aligner=getattr(args, "forced_aligner", None),
        backend=getattr(args, "backend", None),
        two_b_mode=getattr(args, "two_b_mode", None),
        pre_pad=getattr(args, "pre_pad", 0.15),
        post_pad=getattr(args, "post_pad", 0.25),
        min_duration=getattr(args, "min_duration", 1.0),
        no_vad_snap=getattr(args, "no_vad_snap", False),
        device=getattr(args, "device", "cuda:0"),
        dtype=getattr(args, "dtype", "float16"),
        language=getattr(args, "language", "Chinese"),
        gpu_memory_utilization=getattr(args, "gpu_memory_utilization", 0.8),
        interactive_2d=getattr(args, "interactive_2d", False),
        verbose=getattr(args, "verbose", False),
        force_rerun_phases=force_rerun_phases,
    )


def _run_pipeline(args: argparse.Namespace, *, use_tui: bool = False) -> int:
    """流水线主逻辑：基于 SessionController/AppController + 适配器。"""
    from autosmartcut.cli.app_controller import AppController, AppState, SessionController

    # TUI 模式 + 提供了 path 参数 → 使用 AppController 智能识别
    if use_tui and getattr(args, "path", None) is not None:
        return _run_tui_with_path(args)

    # 其他情况（run 模式，或 tui 旧用法）→ 使用 SessionController
    try:
        params = _args_to_params(args)
        ctrl = SessionController()
        ctrl.setup(params)
    except (FileNotFoundError, ValueError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    run = ctrl.run
    session = ctrl.session

    if use_tui:
        setup_logging_tui(run, verbose=params.verbose)
    else:
        setup_logging(run, verbose=params.verbose)

    # 选择适配器
    interactive = use_tui or params.interactive_2d
    if interactive:
        # TUI 旧用法（--input/--manifest 参数）：用 AppController 包装
        from autosmartcut.cli.app_controller import AppController, AppState
        app_ctrl = AppController()
        app_ctrl._run = run
        app_ctrl._session = session
        app_ctrl._cfg = ctrl.cfg
        app_ctrl._set_state(AppState.READY)
        app_ctrl._subscribe_to_session()  # 必须订阅，否则 TUI 收不到任何事件
        from autosmartcut.tui import PipelineApp
        try:
            PipelineApp(app_ctrl).run()
        except Exception as e:
            logger.exception("TUI 流水线失败: %s", e)
            return 1
    else:
        from autosmartcut.cli.cli_adapter import CLIAdapter
        adapter = CLIAdapter(session)
        try:
            adapter.start_sync()
        except Exception as e:
            logger.exception("流水线失败: %s", e)
            return 1

    return 0


def _run_tui_with_path(args: argparse.Namespace) -> int:
    """TUI 模式 + path 参数：使用 AppController 智能识别输入类型。"""
    from autosmartcut.cli.app_controller import AppController, AppState

    ctrl = AppController()
    try:
        ctrl.open(Path(args.path))
    except (FileNotFoundError, ValueError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    # 媒体文件：run 已构造，直接初始化日志
    # 清单文件：run 尚未构造（DIAGNOSING），用清单路径初始化日志
    # 两条路径都在 asyncio.run() 之前完成，确保 stderr sink 已移除、bridge 已配置
    verbose = getattr(args, "verbose", False)
    if ctrl._run is not None:
        setup_logging_tui(ctrl._run, verbose=verbose)
    else:
        mp = ctrl._resolved_input.manifest_path
        setup_logging_tui_for_manifest(mp, verbose=verbose)

    from autosmartcut.tui import PipelineApp
    try:
        PipelineApp(ctrl).run()
    except Exception as e:
        logger.exception("TUI 流水线失败: %s", e)
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
    from autosmartcut.cli.input_resolver import InputType, resolve_input

    # 1. 解析路径 + 推断进度
    try:
        resolved = resolve_input(Path(args.path))
    except (FileNotFoundError, ValueError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    # resume 只接受清单输入
    if resolved.input_type == InputType.MEDIA_FILE:
        print(
            f"错误: resume 命令需要 timeline_manifest.json 或其父文件夹，"
            f"不接受媒体文件。\n"
            f"如需从头开始，请使用: ascut run --input \"{args.path}\" --stage 123",
            file=sys.stderr,
        )
        return 1

    report = resolved.progress_report
    mp = resolved.manifest_path

    # 2. 打印进度摘要
    _print_progress_report(report)

    # 3. 全部完成
    if report.all_completed:
        print("\n所有阶段已完成，无需续跑。")
        print(f"如需重跑 L3: ascut run --manifest \"{mp}\" --stage 3")
        return 0

    # 4. 空骨架（无 annotations）
    if report.suggested_stage is None:
        print("\n清单为空骨架，请使用 ascut run --input <视频> 从头开始。")
        return 1

    # 5. 确定 stage（用户覆盖 > 自动推断）
    stage = args.stage or report.suggested_stage

    # 6. goal 检查
    goal = args.goal or report.goal
    if report.goal_needed and not goal:
        print(
            "\n续跑 L2 需要 --goal 参数，请提供剪辑意图。",
            file=sys.stderr,
        )
        print(f"示例: ascut resume \"{args.path}\" --goal \"保留核心内容\"")
        return 1

    # 7. 确认
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

    # 8. 构造等价 run/tui 参数并递归调用 main()
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
    if getattr(args, "force_rerun", None):
        run_argv += ["--force-rerun", args.force_rerun]
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
