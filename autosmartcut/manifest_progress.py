"""manifest_progress.py — 清单进度推断（UI 无关）。

从 timeline_manifest.json 的 layer_status 与数据字段推断各节点完成状态，
生成结构化的 ProgressReport，供 CLI / TUI / GUI 消费。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autosmartcut.manifest_io import MANIFEST_FILENAME


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class NodeProgress:
    """单个节点的进度状态。"""
    node_id: str            # "l1_perception", "l2b_decision", ...
    display_name: str       # "L1A (ASR 转写)"
    phase: int              # 1, 2, 3
    completed: bool
    completed_at: str | None
    data_valid: bool        # 对应数据完整性校验通过
    summary: str            # "962 条 annotations" / "verdict=pass"


@dataclass
class ProgressReport:
    """清单进度的完整报告，供各 UI 层消费。"""
    manifest_path: Path
    run_id: str
    goal: str
    source_video: str
    duration: float | None
    nodes: list[NodeProgress]
    all_completed: bool
    suggested_stage: str | None     # "3" / "23" / "123" / None
    goal_needed: bool               # 续跑 L2 时需要 goal 但当前为空
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 节点定义表（顺序 = 展示顺序）
# ---------------------------------------------------------------------------

_NODE_DEFS: list[tuple[str, str, int]] = [
    ("l1_perception",      "L1 (识别与对齐)",   1),
    ("l2a_comprehension",  "L2A (语义理解)",    2),
    ("l2b_decision",       "L2B (保留决策)",    2),
    ("l2c_review",         "L2C (审核)",        2),
    ("l2d_human",          "L2D (人工确认)",    2),
    ("l3_execute",         "L3 (执行出片)",     3),
]


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------

def resolve_manifest_path(path: Path) -> Path:
    """接受 timeline_manifest.json 或其父文件夹，返回清单绝对路径。

    Args:
        path: 文件路径或目录路径。

    Returns:
        清单文件的绝对路径。

    Raises:
        FileNotFoundError: 找不到清单文件。
    """
    p = Path(path).resolve()
    if p.is_file():
        return p
    candidate = p / MANIFEST_FILENAME
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(
        f"找不到清单: 尝试了 {p} 和 {candidate}"
    )


# ---------------------------------------------------------------------------
# 进度推断（纯函数）
# ---------------------------------------------------------------------------

def infer_progress(data: dict[str, Any], manifest_path: Path) -> ProgressReport:
    """从清单 dict 推断进度。纯函数，不做任何 IO。

    Args:
        data: 已加载的清单 dict。
        manifest_path: 清单文件路径（仅用于填充报告，不做 IO）。

    Returns:
        ProgressReport 结构化进度报告。
    """
    ls = data.get("layer_status", {})
    if not isinstance(ls, dict):
        ls = {}
    anns = data.get("annotations", [])
    if not isinstance(anns, list):
        anns = []
    current = data.get("current", {})
    if not isinstance(current, dict):
        current = {}
    sm = data.get("source_media", {})
    if not isinstance(sm, dict):
        sm = {}
    warnings: list[str] = []

    # ── 逐节点推断 ──────────────────────────────────────────────
    nodes: list[NodeProgress] = []
    for node_id, display, phase in _NODE_DEFS:
        if node_id == "l3_execute":
            # 调度器写 l3_execute_completed_at；run_execution_layer 曾写 l3_completed_at
            completed_at = ls.get("l3_execute_completed_at") or ls.get("l3_completed_at")
        else:
            ls_key = f"{node_id}_completed_at"
            completed_at = ls.get(ls_key)
        completed = completed_at is not None

        data_valid, summary = _validate_node(
            node_id, data, anns, current, completed
        )

        if completed and not data_valid:
            warnings.append(
                f"{display}: layer_status 标记完成但数据不完整"
            )

        nodes.append(NodeProgress(
            node_id=node_id,
            display_name=display,
            phase=phase,
            completed=completed,
            completed_at=completed_at,
            data_valid=data_valid,
            summary=summary,
        ))

    # ── 推断建议 stage ──────────────────────────────────────────
    completed_ids = {n.node_id for n in nodes if n.completed}
    all_completed = all(n.completed for n in nodes)
    suggested = _suggest_stage(completed_ids, anns, current)

    # goal_needed：续跑需要 L2 且当前 goal 为空
    goal_str = str(data.get("goal", "")).strip()
    goal_needed = (
        suggested is not None
        and "2" in suggested
        and not goal_str
    )

    return ProgressReport(
        manifest_path=manifest_path,
        run_id=str(data.get("run_id", "")),
        goal=goal_str,
        source_video=str(sm.get("path", "")),
        duration=sm.get("duration"),
        nodes=nodes,
        all_completed=all_completed,
        suggested_stage=suggested,
        goal_needed=goal_needed,
        warnings=warnings,
    )


def _validate_node(
    node_id: str,
    data: dict,
    anns: list,
    current: dict,
    completed: bool,
) -> tuple[bool, str]:
    """校验节点对应的数据完整性，返回 (data_valid, summary)。"""
    match node_id:
        case "l1_perception":
            has = (
                len(anns) > 0
                and bool(anns[0].get("content"))
                and anns[0].get("t_start") is not None
                and anns[0].get("t_end") is not None
            )
            count = len(anns)
            if has:
                return True, f"{count} 条 annotations（含时间轴）"
            if len(anns) > 0 and bool(anns[0].get("content")):
                return False, "annotations 无时间戳"
            return False, "无 annotations"

        case "l2a_comprehension":
            comp = current.get("comprehension", {})
            if not isinstance(comp, dict):
                comp = {}
            has = bool(comp.get("purpose"))
            blocks = len(comp.get("outline_blocks", []))
            if has:
                return True, f"{blocks} 个大纲块"
            return False, "无 comprehension"

        case "l2b_decision":
            km = current.get("keep_mask", [])
            if not isinstance(km, list):
                km = []
            has = len(km) > 0
            if has:
                return True, f"{len(km)} 条 keep_mask"
            return False, "无 keep_mask"

        case "l2c_review":
            rr = current.get("review_report", {})
            if not isinstance(rr, dict):
                rr = {}
            verdict = rr.get("verdict", "")
            has = bool(verdict)
            if has:
                return True, f"verdict={verdict}"
            return False, "无 review_report"

        case "l2d_human":
            hfh = current.get("human_feedback_history", [])
            l2d_done = current.get("l2d_completed", False)
            has = bool(l2d_done) or (isinstance(hfh, list) and len(hfh) > 0)
            if has:
                return True, "已确认"
            return False, "未确认"

        case "l3_execute":
            if completed:
                return True, "成片已生成"
            return False, "未出片"

        case _:
            return False, "未知节点"


def _suggest_stage(
    completed: set[str],
    anns: list,
    current: dict,
) -> str | None:
    """根据已完成节点集合推断建议的 --stage 值。

    Returns:
        建议的 stage 字符串，或 None（全部完成 / 需要从头开始）。
    """
    has_l1 = "l1_perception" in completed
    has_l2d = "l2d_human" in completed
    has_l3 = "l3_execute" in completed or "l3" in completed

    if has_l3:
        return None

    if has_l2d:
        return "3"

    if has_l1:
        return "23"

    if len(anns) == 0:
        return None

    return "123"
