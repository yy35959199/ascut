"""由 annotations 派生 tokens；从 manifest 解析视频路径（MVP-mini）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence


def validate_tokens(tokens: list[Any]) -> None:
    """校验 ``tokens`` 为稠密 0..n-1 句面列表。"""
    if not isinstance(tokens, list) or len(tokens) == 0:
        raise ValueError("tokens 须为非空数组")
    for i, item in enumerate(tokens):
        if not isinstance(item, dict):
            raise ValueError(f"tokens[{i}] 须为对象")
        idx = item.get("index")
        if idx != i:
            raise ValueError(f"tokens[{i}].index 须等于列表下标 {i}，实际 {idx!r}")
        tx = item.get("text")
        if not isinstance(tx, str):
            raise ValueError(f"tokens[{i}].text 须为字符串，实际 {type(tx).__name__}")


def tokens_from_annotations(annotations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """从 L1 句级 annotations 生成与 JSON2 等价的稠密 tokens（仅 index + text）。"""
    anns = list(annotations)
    tokens: list[dict[str, Any]] = []
    for i, ann in enumerate(anns):
        tokens.append(
            {
                "index": int(ann.get("index", i)),
                "text": str(ann.get("content", "")),
            }
        )
    validate_tokens(tokens)
    return tokens


def validate_annotations(annotations: list[Any]) -> None:
    """最小校验：非空列表、index 与下标一致。"""
    if not isinstance(annotations, list) or len(annotations) == 0:
        raise ValueError("annotations 须为非空数组")
    for i, ann in enumerate(annotations):
        if not isinstance(ann, dict):
            raise ValueError(f"annotations[{i}] 须为对象")
        if int(ann.get("index", i)) != i:
            raise ValueError(
                f"annotations[{i}].index 须等于 {i}，实际 {ann.get('index')!r}"
            )


def _resolve_source_path(source: str, ref_dir: Path) -> Path:
    p = Path(source)
    if p.is_file():
        return p.resolve()
    cand = ref_dir / source
    if cand.is_file():
        return cand.resolve()
    cand = Path.cwd() / source
    if cand.is_file():
        return cand.resolve()
    raise FileNotFoundError(
        f"找不到源视频: {source!r}（已查 {ref_dir} 与当前工作目录）"
    )


def video_path_from_manifest(data: dict[str, Any], manifest_path: Path) -> Path:
    """优先 source_media.path，否则顶层 source（与 execution.resolve_media_path 语义一致）。"""
    ref = manifest_path.parent.resolve()
    sm = data.get("source_media")
    if isinstance(sm, dict):
        p = sm.get("path")
        if isinstance(p, str) and p.strip():
            return _resolve_source_path(p.strip(), ref)
    src = data.get("source")
    if isinstance(src, str) and src.strip():
        return _resolve_source_path(src.strip(), ref)
    raise ValueError(f"清单缺少 source_media.path 或 source: {manifest_path}")
