"""timeline_manifest.json 读写与校验（MVP-mini，见 doc/AutoSmartCut-MVP-Mini.md）。"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST_FILENAME = "timeline_manifest.json"
MANIFEST_VERSION = "1.0-mini"


def make_manifest_skeleton(
    run_id: str,
    goal: str,
    source_path: str,
    *,
    duration: float | None = None,
) -> dict[str, Any]:
    """创建新清单骨架（L1 前或编排初始化）。"""
    sm: dict[str, Any] = {"path": source_path}
    if duration is not None:
        sm["duration"] = float(duration)
    return {
        "version": MANIFEST_VERSION,
        "run_id": run_id,
        "goal": goal or "",
        "source_media": sm,
        "annotations": [],
        "current": {},
        "layer_status": {},
    }


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"清单不存在: {path}")
    with path.open(encoding="utf-8") as f:
        data: Any = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"清单根节点须为对象: {path}")
    return data


def save_manifest(path: Path, data: dict[str, Any], *, atomic: bool = True) -> None:
    """写入清单；atomic=True 时先写同目录临时文件再 replace（Windows 友好）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if not atomic:
        path.write_text(text, encoding="utf-8")
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        try:
            os.replace(tmp, path)
        except OSError:
            # Windows：目标文件被索引/杀软短暂占用时 os.replace 可能 WinError 5
            shutil.copyfile(tmp, path)
            tmp.unlink(missing_ok=True)
    except Exception:
        if tmp.is_file():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def strip_volatile_fields(data: dict[str, Any]) -> dict[str, Any]:
    """移除不应落盘的运行时字段（就地修改并返回 data）。"""
    # L1A 分块信息：仅用于 L1B 强制对齐，不需要永久保存
    data.pop("l1a_chunks", None)
    cur = data.get("current")
    if isinstance(cur, dict):
        cur.pop("tokens", None)
        cur.pop("cleaned_annotations", None)
        # L2 运行中写入的中间检查点，正式落盘 L2 完成态前清除
        cur.pop("l2_checkpoints", None)
        comp = cur.get("comprehension")
        if isinstance(comp, dict):
            comp.pop("cleaned_annotations", None)
    return data


def write_l2_checkpoint(
    data: dict[str, Any],
    path: Path,
    phase: str,
    payload: dict[str, Any] | None = None,
    *,
    atomic: bool = True,
) -> None:
    """在 L2 子阶段完成后写入 ``current.l2_checkpoints[phase]`` 并原子保存清单。

    ``payload`` 与 ``completed_at`` 一并写入该 phase 的对象中，供排障/断点续跑参考；
    全流程 L2 正常结束时 ``strip_volatile_fields`` 会删除整块 ``l2_checkpoints``。
    """
    cur = data.setdefault("current", {})
    if not isinstance(cur, dict):
        data["current"] = {}
        cur = data["current"]
    cp = cur.setdefault("l2_checkpoints", {})
    if not isinstance(cp, dict):
        cur["l2_checkpoints"] = {}
        cp = cur["l2_checkpoints"]
    entry: dict[str, Any] = {"completed_at": _iso_now()}
    if payload:
        entry.update(payload)
    cp[phase] = entry
    save_manifest(path, data, atomic=atomic)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def validate_manifest_for_l1b(manifest_path: Path) -> None:
    """L1B 前置：清单须含 L1A 产物与可续跑的句级文本。"""
    data = load_manifest(manifest_path)
    raw = data.get("raw_text")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("L1B 需要 manifest 含非空 raw_text（请先执行 L1A 或 --stage 1）")
    anns = data.get("annotations")
    if not isinstance(anns, list) or len(anns) == 0:
        raise ValueError("L1B 需要非空 annotations[]")
    for i, ann in enumerate(anns):
        if not isinstance(ann, dict):
            raise ValueError(f"annotations[{i}] 须为对象")
        if int(ann.get("index", -1)) != i:
            raise ValueError(f"annotations[{i}].index 须为 {i}，实际 {ann.get('index')!r}")
        if "content" not in ann or str(ann.get("content", "")).strip() == "":
            raise ValueError(f"annotations[{i}] 缺少有效 content")


def validate_manifest_for_stages(stages: frozenset[int], data: dict[str, Any]) -> None:
    """按即将执行的阶段校验清单字段。"""
    if 2 in stages:
        anns = data.get("annotations")
        if not isinstance(anns, list) or len(anns) == 0:
            raise ValueError("执行 L2 需要非空 annotations[]")
    if 3 in stages:
        anns = data.get("annotations")
        if not isinstance(anns, list) or len(anns) == 0:
            raise ValueError("执行 L3 需要非空 annotations[]")
        for i, ann in enumerate(anns):
            if not isinstance(ann, dict):
                raise ValueError(f"annotations[{i}] 须为对象")
            if ann.get("t_start") is None or ann.get("t_end") is None:
                raise ValueError(
                    "执行 L3 需要每条 annotation 含 t_start/t_end；"
                    "若仅有 L1A 文本请先执行 --stage 1b 或完整 --stage 1"
                )
        cur = data.get("current")
        if not isinstance(cur, dict):
            raise ValueError("执行 L3 需要 current 对象")
        km = cur.get("keep_mask")
        if not isinstance(km, list) or len(km) == 0:
            raise ValueError("执行 L3 需要 current.keep_mask[]")
        if len(km) != len(anns):
            raise ValueError(
                f"keep_mask 与 annotations 长度不一致: {len(km)} != {len(anns)}"
            )
        for i, entry in enumerate(km):
            if not isinstance(entry, dict):
                raise ValueError(f"keep_mask[{i}] 须为对象")
            if entry.get("index") != i:
                raise ValueError(
                    f"keep_mask[{i}].index 须为 {i}，实际 {entry.get('index')!r}"
                )
            if "keep" not in entry:
                raise ValueError(f"keep_mask[{i}] 缺少 keep")


def touch_layer_status(data: dict[str, Any], layer: str) -> None:
    """写入 layer_status 完成时间戳（layer 为 l1|l1a|l1b|l2|l3）。"""
    ls = data.setdefault("layer_status", {})
    if not isinstance(ls, dict):
        data["layer_status"] = {}
        ls = data["layer_status"]
    key = f"{layer}_completed_at"
    ls[key] = _iso_now()
