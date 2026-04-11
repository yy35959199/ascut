# Demo 2：智能层 — 更新 timeline_manifest.json（2a/2b/2c/2d）
from __future__ import annotations

"""
读取含 ``annotations[]`` 的 **timeline_manifest.json**，经 ``run_intelligence_layer`` 写入 ``current``。

用法（在仓库根目录、已配置 LLM）：
  python demos/demo2_llm.py
  python demos/demo2_llm.py --manifest outputs/timeline_manifest.json --goal "提取核心观点"

默认跳过 2d（等价 ``--auto``）；需要 CLI 审阅时加 ``--interactive-2d``。

下游 Demo 3：
  python demos/demo3_smartcut.py json --manifest outputs/timeline_manifest.json
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
		description="Demo 2：智能层更新 timeline_manifest.json"
	)
	p.add_argument(
		"--manifest",
		type=Path,
		default=Path("outputs/timeline_manifest.json"),
		help="timeline_manifest.json（须含 annotations[]）",
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
		help="启用 2d 人工审阅；默认跳过",
	)
	p.add_argument("--verbose", action="store_true", help="DEBUG 日志")
	return p.parse_args()


def main() -> None:
	args = parse_args()
	run_intelligence_layer(
		args.manifest.resolve(),
		args.goal,
		auto=not args.interactive_2d,
		verbose_log=args.verbose,
		two_b_mode=args.two_b_mode,
	)


if __name__ == "__main__":
	main()
