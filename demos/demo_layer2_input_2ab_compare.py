# Demo：以 timeline_manifest.json 为输入，仅跑 2a+2b，并对比 single / chunked 的 keep_mask
from __future__ import annotations

"""
1. **只调用一次** ``run_2a_comprehension``
2. 分别 ``run_2b_decision(..., mode="single")`` 与 ``mode="chunked"``
3. 写出对比结果 JSON

用法::

    python demos/demo_layer2_input_2ab_compare.py
    python demos/demo_layer2_input_2ab_compare.py --validate-only
"""

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
	sys.path.insert(0, str(_ROOT))

from autosmartcut.annotation_tokens import tokens_from_annotations, validate_tokens
from autosmartcut.manifest_io import load_manifest


def manifest_tokens_workspace(data: dict[str, Any], *, goal: str) -> dict[str, Any]:
	anns = data.get("annotations")
	if not isinstance(anns, list) or not anns:
		raise ValueError("清单须含非空 annotations[]")
	tokens = tokens_from_annotations(anns)
	validate_tokens(tokens)
	src = data.get("source", "")
	if not src and isinstance(data.get("source_media"), dict):
		src = str(data["source_media"].get("path", ""))
	return {
		"tokens": tokens,
		"goal": goal,
		"source": src,
		"language": str(data.get("language", "")),
		"raw_text": str(data.get("raw_text", "")),
	}


def compare_keep_masks(
	a: list[dict[str, Any]], b: list[dict[str, Any]]
) -> dict[str, Any]:
	if len(a) != len(b):
		return {"error": f"长度不一致: {len(a)} vs {len(b)}"}
	disagree: list[int] = []
	for x, y in zip(a, b):
		if x["index"] != y["index"]:
			return {"error": f"index 不对齐: {x['index']} vs {y['index']}"}
		if x["keep"] != y["keep"]:
			disagree.append(int(x["index"]))
	n = len(a)
	return {
		"total": n,
		"agree": n - len(disagree),
		"disagree_count": len(disagree),
		"agreement_rate": (n - len(disagree)) / n if n else 1.0,
		"disagree_indices": disagree,
	}


def parse_args() -> argparse.Namespace:
	p = argparse.ArgumentParser(description="timeline_manifest → 2a + 2b single/chunked 对比")
	p.add_argument(
		"--manifest",
		type=Path,
		default=Path("outputs/timeline_manifest.json"),
		help="须含 annotations[]",
	)
	p.add_argument("--output", type=Path, default=Path("output/layer2_ab_compare.json"))
	p.add_argument("--goal", type=str, default="")
	p.add_argument("--validate-only", action="store_true")
	return p.parse_args()


def main() -> None:
	args = parse_args()
	in_path = args.manifest.resolve()
	if not in_path.is_file():
		raise SystemExit(f"清单不存在: {in_path}")

	data = load_manifest(in_path)
	try:
		_ = manifest_tokens_workspace(data, goal=args.goal)
	except ValueError as e:
		print(f"清单校验失败: {e}")
		raise SystemExit(1) from e

	tokens = tokens_from_annotations(data["annotations"])
	n = len(tokens)
	print(f"[校验] OK: {n} 条 tokens（由 annotations 派生）")

	if args.validate_only:
		return

	from autosmartcut.intelligence_2a import run_2a_comprehension
	from autosmartcut.intelligence_2b import run_2b_decision

	manifest = manifest_tokens_workspace(data, goal=args.goal)
	manifest = run_2a_comprehension(manifest)

	m_single = copy.deepcopy(manifest)
	run_2b_decision(m_single, mode="single")

	m_chunked = copy.deepcopy(manifest)
	run_2b_decision(m_chunked, mode="chunked")

	cmp = compare_keep_masks(m_single["keep_mask"], m_chunked["keep_mask"])
	if "error" in cmp:
		raise RuntimeError(cmp["error"])

	out_doc: dict[str, Any] = {
		"meta": {
			"manifest_path": str(in_path),
			"output_path": str(args.output.resolve()),
			"n_tokens": n,
			"goal": args.goal,
			"two_b_modes_compared": ["single", "chunked"],
			"note": "句面由清单 annotations 派生；时间轴在 annotations。",
		},
		"source": manifest.get("source", ""),
		"comprehension": manifest["comprehension"],
		"keep_mask_single": m_single["keep_mask"],
		"keep_mask_chunked": m_chunked["keep_mask"],
		"comparison": cmp,
	}

	out_path = args.output.resolve()
	out_path.parent.mkdir(parents=True, exist_ok=True)
	with out_path.open("w", encoding="utf-8") as f:
		json.dump(out_doc, f, indent=2, ensure_ascii=False)

	print(
		f"[完成] 写入 {out_path} | "
		f"一致 {cmp['agree']}/{cmp['total']} ({cmp['agreement_rate']:.2%}), "
		f"分歧 {cmp['disagree_count']} 条"
	)


if __name__ == "__main__":
	main()
