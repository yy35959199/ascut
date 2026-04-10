# Demo：以 layer2_input.json（JSON2）为输入，仅跑 2a+2b，并对比 single / chunked 的 keep_mask
from __future__ import annotations

"""
与主流程一致：manifest 仅含 ``tokens[]``（JSON2），不再合成 annotations。

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

from autosmartcut.layer2_tokens import parse_layer2_tokens_document


def layer2_doc_to_manifest(doc: dict[str, Any], *, goal: str) -> dict[str, Any]:
	parse_layer2_tokens_document(doc)
	return {
		"tokens": doc["tokens"],
		"goal": goal,
		"source": doc.get("source", ""),
		"language": doc.get("language", ""),
		"raw_text": doc.get("raw_text", ""),
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
	p = argparse.ArgumentParser(description="JSON2 → 2a + 2b single/chunked 对比")
	p.add_argument("--input", type=Path, default=Path("output/layer2_input.json"))
	p.add_argument("--output", type=Path, default=Path("output/layer2_ab_compare.json"))
	p.add_argument("--goal", type=str, default="")
	p.add_argument("--validate-only", action="store_true")
	return p.parse_args()


def main() -> None:
	args = parse_args()
	in_path = args.input.resolve()
	if not in_path.is_file():
		raise SystemExit(f"输入文件不存在: {in_path}")

	with in_path.open("r", encoding="utf-8") as f:
		doc = json.load(f)

	try:
		parse_layer2_tokens_document(doc)
	except ValueError as e:
		print(f"JSON2 校验失败: {e}")
		raise SystemExit(1) from e

	n = len(doc["tokens"])
	print(f"[校验] OK: {n} 条 tokens，source={doc.get('source', '')!r}")

	if args.validate_only:
		return

	from autosmartcut.intelligence_2a import run_2a_comprehension
	from autosmartcut.intelligence_2b import run_2b_decision

	manifest = layer2_doc_to_manifest(doc, goal=args.goal)
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
			"input_path": str(in_path),
			"output_path": str(args.output.resolve()),
			"n_tokens": n,
			"goal": args.goal,
			"two_b_modes_compared": ["single", "chunked"],
			"note": "智能层仅以 JSON2 tokens 为句面输入；时间轴见 JSON1。",
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
