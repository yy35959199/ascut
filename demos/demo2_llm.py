# Demo 2：智能层 — JSON2 → 2a/2b/2c/2d → JSON3（与 autosmartcut.intelligence 一致）
from __future__ import annotations

"""
与当前 Layer 2 实现一致：读取 **JSON2**（``layer2_input.json``，``tokens[]`` 仅 index+text），
经编排后写出仅含 ``keep_mask`` 的 JSON3。

入口：
  - ``source``: 源视频路径字符串（供流水线；智能层不剪片）
  - ``tokens[]``: 每项 ``index``、``text``（须稠密 0..n-1）

出口（JSON3）：
  - ``keep_mask``: [ { "index": int, "keep": bool }, ... ]，长度与 tokens 相同

用法（在仓库根目录、已配置 LLM）：
  python demos/demo2_llm.py
  python demos/demo2_llm.py --layer2 output/layer2_input.json --output output/layer2_output.json --goal "提取核心观点"

默认跳过 2d 人工审阅（等价 ``--auto``）；需要 CLI 审阅时加 ``--interactive-2d``。

下游 Demo 3：
  python demos/demo3_smartcut.py json --layer1 output/layer1_annotations.json --mask output/layer2_output.json

等价入口：
  python -m autosmartcut.intelligence <layer2_input.json> <output.json> [--goal "..."]

环节划分：本目录仅 ``demo1_asr`` / ``demo2_llm`` / ``demo3_smartcut`` 对应 L1/L2/L3；
mock JSON 等辅助脚本在 ``demos/tools/``。
"""

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
	sys.path.insert(0, str(_ROOT))

from autosmartcut.intelligence import run_intelligence_layer


def parse_args() -> argparse.Namespace:
	p = argparse.ArgumentParser(
		description="Demo 2：智能层 JSON2→JSON3（keep_mask），封装 intelligence 主流程"
	)
	p.add_argument(
		"--layer2",
		type=Path,
		default=Path("output/layer2_input.json"),
		help="JSON2 句面路径（默认 output/layer2_input.json）",
	)
	p.add_argument(
		"--output",
		type=Path,
		default=Path("output/layer2_output.json"),
		help="JSON3 输出路径",
	)
	p.add_argument(
		"--goal",
		type=str,
		default="",
		help="分析/剪辑目标",
	)
	p.add_argument(
		"--two-b-mode",
		type=str,
		choices=["single", "chunked"],
		default="single",
		help="2b：single 全文单次；chunked 按 2a 分块多次调用",
	)
	p.add_argument(
		"--interactive-2d",
		action="store_true",
		help="启用 2d 人工审阅；默认跳过（与 --auto 等价，适合批处理/CI）",
	)
	return p.parse_args()


def main() -> None:
	args = parse_args()
	run_intelligence_layer(
		args.layer2.resolve(),
		args.output.resolve(),
		args.goal,
		auto=not args.interactive_2d,
		two_b_mode=args.two_b_mode,
	)


if __name__ == "__main__":
	main()
