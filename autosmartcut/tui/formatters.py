"""tui/formatters.py — 兼容转发层。

实现已迁至 ``autosmartcut.formatters``；此处保留重导出以免旧 import 路径断裂。
"""
from __future__ import annotations

from autosmartcut.cli.formatters import (
    L1aChunkDoneState,
    L1aChunkProgressState,
    format_decision_list,
    format_progress,
    format_review_summary,
    format_stats,
    parse_l1a_chunk_done,
    parse_l1a_intra_chunk_progress,
)

_format_progress = format_progress
_format_review_summary = format_review_summary
_format_decision_list = format_decision_list
_format_stats = format_stats
_parse_l1a_intra_chunk_progress = parse_l1a_intra_chunk_progress
_parse_l1a_chunk_done = parse_l1a_chunk_done

__all__ = [
    "L1aChunkProgressState",
    "L1aChunkDoneState",
    "parse_l1a_intra_chunk_progress",
    "parse_l1a_chunk_done",
    "format_review_summary",
    "format_decision_list",
    "format_stats",
    "format_progress",
]
