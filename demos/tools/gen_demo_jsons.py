"""从 Demo1 完整 JSON 生成 timeline_manifest.json（含 mock keep_mask）。

辅助工具；与 ``demo1_asr.py`` 产出的清单格式对齐。

用法（在仓库根目录）::

    python demos/tools/gen_demo_jsons.py
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path
from typing import Any

from ulid import ULID

from autosmartcut.manifest_io import make_manifest_skeleton, save_manifest
from autosmartcut.perception import compact_annotations


def gen_mock_keep_mask(
    layer1: list[dict[str, Any]],
    *,
    cut_ratio: float = 0.35,
    min_run: int = 3,
    max_run: int = 12,
    seed: int = 42,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    speech_indices = [ann["index"] for ann in layer1]
    n_speech = len(speech_indices)
    cut_set: set[int] = set()

    if n_speech == 0:
        return [{"index": ann["index"], "keep": True} for ann in layer1]

    target_cut = min(int(n_speech * cut_ratio), n_speech)
    attempts = 0
    while len(cut_set) < target_cut and attempts < 1000:
        start = rng.randint(0, n_speech - 1)
        run_len = rng.randint(min_run, max_run)
        for k in range(start, min(start + run_len, n_speech)):
            cut_set.add(speech_indices[k])
        attempts += 1

    keep_mask: list[dict[str, Any]] = []
    for ann in layer1:
        keep = ann["index"] not in cut_set
        keep_mask.append({"index": ann["index"], "keep": keep})
    return keep_mask


def main() -> None:
    p = argparse.ArgumentParser(description="从 demo1 完整 JSON 生成 timeline_manifest.json")
    p.add_argument(
        "--input",
        type=Path,
        default=Path("outputs/demo1_annotations_full.json"),
        help="Demo1 输出的完整 JSON（含 metadata）",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=Path("outputs"),
        help="输出目录",
    )
    p.add_argument("--cut-ratio", type=float, default=0.35)
    p.add_argument("--min-run", type=int, default=3)
    p.add_argument("--max-run", type=int, default=12)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    raw = json.loads(args.input.read_text(encoding="utf-8"))
    source = raw.get("source", "")
    annotations = raw["annotations"]

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    full_path = out_dir / "layer1_full.json"
    shutil.copyfile(args.input, full_path)

    layer1 = compact_annotations(annotations)
    keep_mask = gen_mock_keep_mask(
        layer1,
        cut_ratio=args.cut_ratio,
        min_run=args.min_run,
        max_run=args.max_run,
        seed=args.seed,
    )

    rid = str(ULID())
    m = make_manifest_skeleton(rid, "", str(source))
    m["annotations"] = layer1
    m["current"] = {"keep_mask": keep_mask}
    mp = out_dir / "timeline_manifest.json"
    save_manifest(mp, m, atomic=True)

    n_speech = len(layer1)
    n_keep = sum(1 for x in keep_mask if x["keep"] is True)
    n_cut = sum(1 for x in keep_mask if x["keep"] is False)
    print(f"总 annotations: {len(layer1)} (speech={n_speech})")
    print(f"mock keep_mask: keep={n_keep}, cut={n_cut}")
    print(f"已写入: {full_path.name}, timeline_manifest.json -> {out_dir}")


if __name__ == "__main__":
    main()
