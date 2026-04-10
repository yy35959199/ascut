"""Layer 2 句面输入（JSON2）：校验与加载。

智能层 **仅** 以此结构的 ``tokens[]`` 为句面真值；时间轴由 Layer1 JSON1 供执行层使用。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def validate_tokens(tokens: list[Any]) -> None:
    """校验 ``tokens`` 为稠密 0..n-1 句面列表。

    Raises:
        ValueError: 结构不合法
    """
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


def parse_layer2_tokens_document(data: dict[str, Any]) -> dict[str, Any]:
    """校验整份 JSON2 文档，返回原字典（调用方可继续读 source 等）。"""
    if not isinstance(data, dict):
        raise ValueError("JSON2 根节点须为对象")
    tokens = data.get("tokens")
    if not isinstance(tokens, list):
        raise ValueError("JSON2 缺少 tokens 数组")
    validate_tokens(tokens)
    return data


def load_layer2_tokens_document(path: Path) -> dict[str, Any]:
    """从磁盘加载 JSON2（``source`` + ``tokens[]``）。"""
    if not path.is_file():
        raise FileNotFoundError(f"Layer2 句面文件不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return parse_layer2_tokens_document(data)


def video_path_from_tokens_json(tokens_json: Path) -> Path:
    """从 JSON2 的 ``source`` 字段解析视频路径（与 execution.resolve_media_path 语义一致）。"""
    doc = load_layer2_tokens_document(tokens_json)
    src = doc.get("source")
    if not src or not isinstance(src, str):
        raise ValueError(f"JSON2 缺少合法 source 字段: {tokens_json}")
    p = Path(src)
    if p.is_file():
        return p.resolve()
    cand = tokens_json.parent / src
    if cand.is_file():
        return cand.resolve()
    cand = Path.cwd() / src
    if cand.is_file():
        return cand.resolve()
    raise FileNotFoundError(
        f"找不到源视频: {src!r}（相对 {tokens_json.parent} 或当前工作目录）"
    )
