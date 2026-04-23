"""三层流水线编排与 CLI 入口（ascut，MVP-mini）。

入口::

    ascut run [--stage SPEC] [--input|--manifest ...]

``--stage``：``1`` | ``2`` | ``3`` | ``12`` | ``23`` | ``123`` | ``1a`` | ``1b`` | ``1a2`` | ``1b2`` | ``1a23`` | ``1b23``；省略且未指定 ``--from-stage`` 时等价全流程 ``123``。
``--from-stage`` 已弃用，映射为等价 ``--stage``（见 doc/AutoSmartCut-MVP-Mini.md）。

单一持久化文件：``timeline_manifest.json``（见 ``autosmartcut.manifest_io``）。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import warnings
from pathlib import Path

from autosmartcut.config import load_config
from autosmartcut.dual_track_orchestrator import run_dual_track_after_l1a
from autosmartcut.execution import run_execution_layer
from autosmartcut.intelligence import run_intelligence_layer
from autosmartcut.log import setup_logging
from autosmartcut.manifest_io import load_manifest, validate_manifest_for_stages
from autosmartcut.manifest_stages import infer_l1_mode, resolve_stages, validate_cli_args
from autosmartcut.perception import (
	run_l1a_asr_only,
	run_l1b_align_only,
	run_perception_layer,
)
from autosmartcut.pipeline_run import PipelineRun

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ascut",
        description="AutoSmartCut：识别层 → 智能层 → 执行层（timeline_manifest.json）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("run", help="单视频流水线（按 --stage 子集执行）")
    pr.add_argument(
        "--stage",
        type=str,
        default=None,
        metavar="SPEC",
        help="1|2|3|12|23|123|1a|1b|1a2|1b2|1a23|1b23；省略且未指定 --from-stage 时默认全流程 123",
    )
    pr.add_argument(
        "--from-stage",
        type=int,
        choices=[1, 2, 3],
        default=None,
        help="已弃用：1→123，2→23，3→3；请改用 --stage",
    )
    pr.add_argument(
        "--input",
        type=Path,
        default=None,
        help="输入视频（--stage 含 1 时必填）",
    )
    pr.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="timeline_manifest.json（--stage 不以 1 开头时必填）",
    )
    pr.add_argument("--goal", type=str, default="", help="智能层目标（L2 使用）")
    pr.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="产物目录；含 1 且省略时 <视频父目录>/ascut_out_<ULID 前8位>",
    )
    pr.add_argument(
        "--output-name",
        type=str,
        default=None,
        help="输出视频文件名（basename），落在 output_dir",
    )
    pr.add_argument(
        "--interactive-2d",
        action="store_true",
        help="启用 2d CLI 人工审阅；默认 auto 跳过",
    )
    pr.add_argument(
        "--two-b-mode",
        type=str,
        choices=["single", "chunked"],
        default="single",
        help="2b：single 或 chunked",
    )
    pr.add_argument("--config", type=Path, default=None, help="config.toml")
    pr.add_argument("--asr-model", type=Path, default=None, help="Qwen3-ASR 模型目录")
    pr.add_argument("--forced-aligner", type=Path, default=None, help="ForcedAligner 目录")
    pr.add_argument(
        "--backend",
        type=str,
        choices=["transformers", "vllm"],
        default="transformers",
    )
    pr.add_argument("--device", type=str, default="cuda:0")
    pr.add_argument(
        "--dtype",
        type=str,
        choices=["float16", "bfloat16", "float32"],
        default="float16",
    )
    pr.add_argument("--language", type=str, default="Chinese")
    pr.add_argument("--gpu-memory-utilization", type=float, default=0.8)
    pr.add_argument("--pre-pad", type=float, default=0.15)
    pr.add_argument("--post-pad", type=float, default=0.25)
    pr.add_argument("--min-duration", type=float, default=1.0)
    pr.add_argument(
        "--no-vad-snap",
        action="store_true",
        help="关闭 L3 VAD 切点吸附",
    )
    pr.add_argument(
        "--no-parallel-l1b-l2",
        action="store_true",
        help="关闭 L1A 后 L1B 与 L2 双轨并行（恢复为仅串行跑 L2）",
    )
    pr.add_argument("--verbose", action="store_true", help="DEBUG 日志")

    # 占位：validate_cli_args 曾检查旧参数；runner 不再注册 layer*-json
    pr.set_defaults(layer1_json=None, layer2_json=None, layer3_json=None)

    args = parser.parse_args(argv)

    with warnings.catch_warnings():
        warnings.simplefilter("always", DeprecationWarning)
        try:
            stages = resolve_stages(args)
        except ValueError as e:
            pr.error(str(e))
    validate_cli_args(stages, args, pr)

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


def _run_pipeline(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    asr_model = args.asr_model if args.asr_model is not None else cfg.models.asr_model_path
    forced = (
        args.forced_aligner
        if args.forced_aligner is not None
        else cfg.models.forced_aligner_path
    )

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

    setup_logging(run, verbose=args.verbose)

    t0 = time.perf_counter()
    auto = not args.interactive_2d

    try:
        l1_mode = infer_l1_mode(args, stages)
        dual_done = False
        if l1_mode == "both" and 1 in stages:
            run_perception_layer(
                run,
                asr_model_path=asr_model,
                forced_aligner_path=forced,
                config=cfg,
                backend=args.backend,
                device=args.device,
                dtype=args.dtype,
                language=args.language,
                gpu_memory_utilization=args.gpu_memory_utilization,
            )
        elif l1_mode == "a" and 1 in stages:
            run_l1a_asr_only(
                run,
                asr_model_path=asr_model,
                config=cfg,
                backend=args.backend,
                device=args.device,
                dtype=args.dtype,
                language=args.language,
                gpu_memory_utilization=args.gpu_memory_utilization,
            )
            if (
                cfg.execution.parallel_l1b_l2_enabled
                and not args.no_parallel_l1b_l2
                and 2 in stages
            ):
                run_dual_track_after_l1a(
                    run,
                    forced_aligner_path=forced,
                    config=cfg,
                    auto=auto,
                    two_b_mode=args.two_b_mode,
                    backend=args.backend,
                    device=args.device,
                    dtype=args.dtype,
                    language=args.language,
                    gpu_memory_utilization=args.gpu_memory_utilization,
                    post_merge_validate_stages=(
                        frozenset({3}) if 3 in stages else frozenset({2})
                    ),
                )
                dual_done = True
        elif l1_mode == "b":
            run_l1b_align_only(
                run,
                forced_aligner_path=forced,
                config=cfg,
                backend=args.backend,
                device=args.device,
                dtype=args.dtype,
                language=args.language,
                gpu_memory_utilization=args.gpu_memory_utilization,
            )

        if 2 in stages and not dual_done:
            run_intelligence_layer(
                run.manifest_path,
                run.goal,
                auto=auto,
                verbose_log=args.verbose,
                two_b_mode=args.two_b_mode,
            )

        if 3 in stages:
            validate_manifest_for_stages(
                frozenset({3}), load_manifest(run.manifest_path)
            )
            run_execution_layer(
                run,
                config=cfg,
                pre_pad=args.pre_pad,
                post_pad=args.post_pad,
                min_duration=args.min_duration,
                gap_after_cap=None,
                vad_snap_disabled_by_cli=args.no_vad_snap,
            )

        elapsed = time.perf_counter() - t0
        logger.info(
            "=== AutoSmartCut 完成 | run_id=%s | stages=%s | 耗时 %.1fs | 输出=%s ===",
            run.run_id,
            sorted(stages),
            elapsed,
            run.output_video if 3 in stages else run.manifest_path,
        )
        return 0
    except Exception as e:
        logger.exception("流水线失败: %s", e)
        return 1


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.command == "run":
        code = _run_pipeline(args)
        raise SystemExit(code)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
