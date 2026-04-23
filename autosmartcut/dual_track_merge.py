"""双轨 partial 路径与合并（无重依赖，便于单测）。"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from autosmartcut.pipeline_run import PipelineRun

MERGE_SUBDIR = ".ascut_merge"
L1B_PARTIAL_NAME = "l1b.partial.json"
L2_PARTIAL_NAME = "l2.partial.json"


def merge_dir_for_run(run: PipelineRun) -> Path:
    return run.output_dir / MERGE_SUBDIR / run.run_id


def atomic_write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.replace(tmp, path)
    except OSError:
        shutil.copyfile(tmp, path)
        tmp.unlink(missing_ok=True)


def merge_partials_into_manifest(
    base: dict[str, Any],
    l1b_partial: dict[str, Any],
    l2_partial: dict[str, Any],
) -> dict[str, Any]:
    """先应用 L1B partial，再应用 L2 partial；就地修改 base 并返回。"""
    an = l1b_partial.get("annotations")
    if isinstance(an, list):
        base["annotations"] = an
    ls = l1b_partial.get("layer_status")
    if isinstance(ls, dict):
        b_ls = base.setdefault("layer_status", {})
        if not isinstance(b_ls, dict):
            base["layer_status"] = {}
            b_ls = base["layer_status"]
        for k, v in ls.items():
            b_ls[k] = v

    cur = l2_partial.get("current")
    if isinstance(cur, dict):
        base["current"] = cur
    g = l2_partial.get("goal")
    if isinstance(g, str):
        base["goal"] = g
    ls2 = l2_partial.get("layer_status")
    if isinstance(ls2, dict):
        b_ls = base.setdefault("layer_status", {})
        if not isinstance(b_ls, dict):
            base["layer_status"] = {}
            b_ls = base["layer_status"]
        for k, v in ls2.items():
            b_ls[k] = v
    return base
