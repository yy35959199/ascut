"""三层流水线编排与 CLI 入口（ascut）。

================================================================================
命令行总览
================================================================================

  入口：控制台脚本 ``ascut``（见 pyproject ``[project.scripts]``），或：

    python -m autosmartcut.runner run <参数...>

  必须带子命令 ``run``；当前仅实现 ``run``，后续可扩展其它子命令。

  基本形式::

    ascut run [--from-stage N] [各阶段相关参数] [共用参数] [L1/L3 调参]

================================================================================
--from-stage：从哪一阶段开始跑（核心分流）
================================================================================

  +-------------+------------------------------------------+----------------+
  | from-stage  | 会执行的阶段                              | 典型用途        |
  +=============+==========================================+================+
  | 1（默认）    | L1 识别 → L2 智能 → L3 执行               | 从原始视频全流程 |
  +-------------+------------------------------------------+----------------+
  | 2           | 跳过 L1；L2 → L3                          | 已有 JSON1      |
  +-------------+------------------------------------------+----------------+
  | 3           | 跳过 L1、L2；仅 L3                        | 已有 JSON1+JSON3|
  +-------------+------------------------------------------+----------------+

  阶段与代码对应：

  - **L1**：``run_perception_layer`` — ASR / 对齐 / 句级标注，写出 JSON1（及 JSON2 辅助文件）。
  - **L2**：``run_intelligence_layer`` — LLM + 可选 2d；写出 JSON3（``keep_mask``）。
  - **L3**：``run_execution_layer`` — 读 JSON1+JSON3，smartcut 出成片。

  **互斥约束（由 ``_validate_run_args`` 强制）：**

  - ``from-stage 1``：必须 ``--input``；**不得**出现 ``--layer1-json`` / ``--layer3-json``。
  - ``from-stage 2``：必须 ``--layer1-json``；**不得** ``--input``、``--layer3-json``。
  - ``from-stage 3``：必须 ``--layer1-json`` 与 ``--layer3-json``；**不得** ``--input``。

  从 stage 2/3 开始时，**源视频路径**一律从 JSON1 字段 ``source`` 解析（与
  ``execution.resolve_media_path`` 一致：绝对路径、相对 JSON1 目录、相对 CWD）。

================================================================================
各参数含义与约束（按类别）
================================================================================

【与阶段强相关】

  --from-stage {1,2,3}
      默认 1。决定从哪段逻辑进入流水线（见上表）。

  --input PATH
      **仅 from-stage 1**。输入视频文件；将用于 L1 解码与写入 JSON1 的 ``source``。

  --layer1-json PATH
      **from-stage 2 或 3 必填**。Layer1 产出的 JSON1（如 ``layer1_annotations.json``），
      须含 ``annotations[]``、``source`` 等契约字段。

  --layer3-json PATH
      **仅 from-stage 3 必填**。Layer2 产出 JSON3，顶层含 ``keep_mask[]``，与 JSON1
      句级 ``index`` 对齐。

  --goal TEXT
      传入智能层；**from-stage 3 时无效**（不跑 L2）。建议 L2 场景下填写剪辑/摘要目标。
      from-stage 2 且留空时由调用方自担模型效果风险。

【产物与输出视频】

  --output-dir PATH
      产物目录（日志、可选 JSON、**最终成片**均相对此目录，除非 JSON 路径显式在外）。
      - stage 1 且省略：``<视频父目录>/ascut_out_<ULID 前 8 位>``。
      - stage 2/3 且省略：**默认 JSON1 文件所在目录**。

  --output-name BASENAME
      仅最终**视频文件名**（须含扩展名，如 ``out.mp4``）；禁止路径分隔符（实现中会取
      ``Path(name).name`` 防穿越）。文件落在 ``output_dir`` 下。
      省略时默认 ``<源视频 stem>_cut<源后缀>``（无后缀则 ``.mp4``）。

【L2 行为】

  --interactive-2d
      若指定：跑 2d CLI 人工改 ``keep_mask``；**默认不指定** = auto，跳过 2d，
      直接使用 2b 的 ``keep_mask``。

【配置与 L1 模型（stage 1 才真正跑 L1；其它阶段可忽略但仍可出现在命令行）】

  --config PATH
      ``config.toml``；省略则用包相对默认路径（见 ``autosmartcut.config``）。

  --asr-model / --forced-aligner
      覆盖 config 里 ``[models]`` 的模型目录；仅 L1 使用。

  --backend {transformers,vllm}
  --device / --dtype / --language / --gpu-memory-utilization
      Qwen3-ASR 推理相关；主要影响 L1。

【L3 剪切调参（任意会跑 L3 的阶段均生效）】

  --pre-pad / --post-pad / --min-duration
      见 ``execution.keep_mask_to_positive_segments``：段前/后伸展、过短段合并。

【日志】

  --verbose
      DEBUG 级日志；同时影响 ``ensure_autosmartcut_logging`` 的默认级别（L2 单独跑时）。

================================================================================
三阶段起始 · 命令示例（Windows 路径请按本机修改）
================================================================================

  # --- Stage 1：全流程（默认），从原始视频跑到成片 ---
  ascut run --input samples\\alxe_01.mp4
  ascut run --input samples\\alxe_01.mp4 --output-dir output --output-name final.mp4
  ascut run --input samples\\alxe_01.mp4 --goal "提取核心观点" --verbose

  # --- Stage 2：已有 JSON1，只跑智能层 + 执行层 ---
  ascut run --from-stage 2 --layer1-json output\\layer1_annotations.json --goal "精华剪辑"
  ascut run --from-stage 2 --layer1-json D:\\data\\l1.json --output-dir D:\\out --output-name cut.mp4

  # --- Stage 3：已有 JSON1 与 JSON3（keep_mask），只跑执行层 ---
  ascut run --from-stage 3 ^
      --layer1-json output\\layer1_annotations.json ^
      --layer3-json output\\layer2_output.json ^
      --output-dir output --output-name only_l3.mp4

================================================================================
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from autosmartcut.config import load_config
from autosmartcut.execution import run_execution_layer
from autosmartcut.intelligence import run_intelligence_layer
from autosmartcut.log import setup_logging
from autosmartcut.perception import run_perception_layer
from autosmartcut.pipeline_run import PipelineRun

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 与「模块文档字符串」中的约束表一致；报错信息面向终端用户。
# ---------------------------------------------------------------------------
def _validate_run_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
	stage = args.from_stage
	if stage == 1:
		if args.input is None:
			parser.error("--from-stage 1（默认）时必须提供 --input")
		if args.layer1_json is not None or args.layer3_json is not None:
			parser.error("--from-stage 1 时不要使用 --layer1-json / --layer3-json")
	elif stage == 2:
		if args.layer1_json is None:
			parser.error("--from-stage 2 时必须提供 --layer1-json")
		if args.layer3_json is not None:
			parser.error("--from-stage 2 时不要提供 --layer3-json（由 L2 生成）")
		if args.input is not None:
			parser.error("--from-stage 2 时不要提供 --input（源视频从 JSON1 的 source 解析）")
	elif stage == 3:
		if args.layer1_json is None or args.layer3_json is None:
			parser.error("--from-stage 3 时必须同时提供 --layer1-json 与 --layer3-json")
		if args.input is not None:
			parser.error("--from-stage 3 时不要提供 --input（源视频从 JSON1 的 source 解析）")


# ---------------------------------------------------------------------------
# 参数定义：help 为简要说明；完整语义见文件顶部文档字符串。
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		prog="ascut",
		description="AutoSmartCut：识别层 → 智能层 → 执行层",
	)
	sub = parser.add_subparsers(dest="command", required=True)

	pr = sub.add_parser("run", help="单视频流水线（可按阶段起始）")
	pr.add_argument(
		"--from-stage",
		type=int,
		choices=[1, 2, 3],
		default=1,
		help="1=全流程 L1→L2→L3；2=从智能层起（需已有 JSON1）；3=从执行层起（需 JSON1+JSON3）",
	)
	pr.add_argument(
		"--input",
		type=Path,
		default=None,
		help="输入视频（仅 --from-stage 1 需要）",
	)
	pr.add_argument(
		"--layer1-json",
		type=Path,
		default=None,
		help="Layer1 输出 JSON1 路径（--from-stage 2 或 3）",
	)
	pr.add_argument(
		"--layer3-json",
		type=Path,
		default=None,
		help="Layer2 输出 JSON3（keep_mask）路径（仅 --from-stage 3）",
	)
	pr.add_argument("--goal", type=str, default="", help="智能层目标（L2 需要；from-stage 3 忽略）")
	pr.add_argument(
		"--output-dir",
		type=Path,
		default=None,
		help="产物目录；stage1 默认 <视频同目录>/ascut_out_<ULID 前 8 位>；stage2/3 默认 JSON1 所在目录",
	)
	pr.add_argument(
		"--output-name",
		type=str,
		default=None,
		help="输出视频文件名（仅 basename），写入产物目录",
	)
	pr.add_argument(
		"--interactive-2d",
		action="store_true",
		help="启用 2d CLI 人工审阅；默认跳过（auto）",
	)
	pr.add_argument(
		"--config",
		type=Path,
		default=None,
		help="config.toml 路径（默认项目根目录）",
	)
	pr.add_argument(
		"--asr-model",
		type=Path,
		default=None,
		help="Qwen3-ASR 模型目录（默认 config [models]）",
	)
	pr.add_argument(
		"--forced-aligner",
		type=Path,
		default=None,
		help="ForcedAligner 模型目录（默认 config [models]）",
	)
	pr.add_argument(
		"--backend",
		type=str,
		choices=["transformers", "vllm"],
		default="transformers",
		help="ASR 推理后端",
	)
	pr.add_argument(
		"--device",
		type=str,
		default="cuda:0",
		help="transformers 下 device_map",
	)
	pr.add_argument(
		"--dtype",
		type=str,
		choices=["float16", "bfloat16", "float32"],
		default="float16",
	)
	pr.add_argument(
		"--language",
		type=str,
		default="Chinese",
		help="ASR 语言",
	)
	pr.add_argument(
		"--gpu-memory-utilization",
		type=float,
		default=0.8,
		help="vLLM 显存占用比例",
	)
	pr.add_argument(
		"--pre-pad",
		type=float,
		default=0.15,
		help="L3 保留段前 padding（秒）",
	)
	pr.add_argument(
		"--post-pad",
		type=float,
		default=0.25,
		help="L3 保留段后 padding（秒）",
	)
	pr.add_argument(
		"--min-duration",
		type=float,
		default=1.0,
		help="L3 过短区间合并阈值（秒）",
	)
	pr.add_argument(
		"--verbose",
		action="store_true",
		help="DEBUG 级日志",
	)

	args = parser.parse_args(argv)
	_validate_run_args(parser, args)
	return args


# ---------------------------------------------------------------------------
# 构造 PipelineRun：绑定本轮 ULID、显式 json1/json3 路径、日志与成片路径。
# ---------------------------------------------------------------------------
def _build_run(args: argparse.Namespace) -> PipelineRun:
	if args.from_stage == 1:
		return PipelineRun.new(
			video_path=args.input,
			goal=args.goal,
			output_dir=args.output_dir,
			output_video_name=args.output_name,
		)
	if args.from_stage == 2:
		return PipelineRun.from_stage2(
			layer1_json=args.layer1_json,
			goal=args.goal,
			output_dir=args.output_dir,
			output_video_name=args.output_name,
		)
	return PipelineRun.from_stage3(
		layer1_json=args.layer1_json,
		layer3_json=args.layer3_json,
		output_dir=args.output_dir,
		output_video_name=args.output_name,
	)


# ---------------------------------------------------------------------------
# stage<=1 → L1；stage<=2 → L2；始终执行 L3（若前置阶段被跳过则直接读盘上 JSON）。
# ---------------------------------------------------------------------------
def _run_pipeline(args: argparse.Namespace) -> int:
	cfg = load_config(args.config)
	asr_model = args.asr_model if args.asr_model is not None else cfg.models.asr_model_path
	forced = (
		args.forced_aligner
		if args.forced_aligner is not None
		else cfg.models.forced_aligner_path
	)

	try:
		run = _build_run(args)
	except (FileNotFoundError, ValueError) as e:
		print(f"错误: {e}", file=sys.stderr)
		return 1

	setup_logging(run, verbose=args.verbose)

	t0 = time.perf_counter()
	auto = not args.interactive_2d
	stage = args.from_stage

	try:
		if stage <= 1:
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

		if stage <= 2:
			run_intelligence_layer(
				run.json1_path,
				run.json3_path,
				run.goal,
				auto=auto,
				verbose_log=args.verbose,
			)

		run_execution_layer(
			run,
			config=cfg,
			pre_pad=args.pre_pad,
			post_pad=args.post_pad,
			min_duration=args.min_duration,
			gap_after_cap=None,
		)

		elapsed = time.perf_counter() - t0
		logger.info(
			"=== AutoSmartCut 完成 | run_id=%s | from_stage=%s | 耗时 %.1fs | 输出=%s ===",
			run.run_id,
			stage,
			elapsed,
			run.output_video,
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
