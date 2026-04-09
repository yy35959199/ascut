# Demo 2：智能层 — JSON1 → 2a/2b/2c/2d → JSON3（与 autosmartcut.intelligence 一致）
from __future__ import annotations

"""
与当前 Layer 2 实现一致：读取 Layer 1 产出的 JSON1，经编排后写出仅含 keep_mask 的 JSON3。

入口（JSON1，与 write_perception_outputs / perception 契约一致）：
  - source: str（视频路径，供 Layer 3 解析）
  - annotations[]: index（须与列表下标 0..n-1 一致）, t_start, t_end, content, gap_after, confidence, …

出口（JSON3，与 save_layer2_json / execution 契约一致）：
  - keep_mask: [ { "index": int, "keep": bool }, ... ]，长度与 annotations 相同

用法（在仓库根目录、已配置 LLM）：
  python demos/demo2_llm.py
  python demos/demo2_llm.py --layer1 outputs/layer1_annotations.json --output outputs/layer2_output.json --goal "提取核心观点"

下游 Demo 3：
  python demos/demo3_smartcut.py json --layer1 outputs/layer1_annotations.json --mask outputs/layer2_output.json

等价入口（无本脚本时）：
  python -m autosmartcut.intelligence <layer1.json> <output.json> [--goal "..."]
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
		description="Demo 2：智能层 JSON1→JSON3（keep_mask），封装 intelligence 主流程"
	)
	p.add_argument(
		"--layer1",
		type=Path,
		default=Path("outputs/layer1_annotations.json"),
		help="Layer 1 输出的 JSON1（默认与 demo1 写入的标准文件名一致）",
	)
	p.add_argument(
		"--output",
		type=Path,
		default=Path("outputs/layer2_output.json"),
		help="Layer 2 输出的 JSON3 路径（仅写入 keep_mask）",
	)
	p.add_argument(
		"--goal",
		type=str,
		default="",
		help="分析/剪辑目标，传入 LLM（与 python -m autosmartcut.intelligence --goal 相同）",
	)
	return p.parse_args()


def main() -> None:
	args = parse_args()
	run_intelligence_layer(args.layer1.resolve(), args.output.resolve(), args.goal)


if __name__ == "__main__":
	main()
