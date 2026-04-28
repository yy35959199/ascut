"""Layer 2 / 2d Core API — 纯函数式 API

## 职责
接收 manifest_dict 和 Action 对象，返回更新后的 manifest_dict 和控制信号。
不做任何 I/O 操作（不读写文件、不调用 input()、不打印）。

## 接口
run_2d(manifest_dict, action) -> CoreResult

## 注意
- 所有操作均为纯函数，无副作用（仅 logging）
- overrides 采用 delta 追加模式
- tokens 只读，不被修改
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


logger = logging.getLogger(__name__)


# ============================================================================
# 信号枚举
# ============================================================================

class Signal(Enum):
    """2d Core 返回给编排层的控制信号"""
    CONTINUE = "continue"       # 继续 2d 交互循环
    DONE = "done"               # 用户确认，流转 Layer 3
    QUIT = "quit"               # 用户取消
    REFLOW_2A = "reflow_2a"     # F1/F2 反馈，需回流 2a
    REFLOW_2B = "reflow_2b"     # F3 反馈，需回流 2b


# ============================================================================
# Action 数据类
# ============================================================================

class FeedbackType(Enum):
    """四种结构化反馈通道"""
    F1_PURPOSE_DRIFT = "f1_purpose_drift"           # 主旨偏差
    F2_KEYWORD_ERROR = "f2_keyword_error"            # 关键词识别错误
    F3_SELECTION_OPINION = "f3_selection_opinion"     # 内容选择意见
    F4_TIME_POINT = "f4_time_point"                  # 剪辑时间节点意见


@dataclass(frozen=True)
class ToggleAction:
    """切换单个 index 的 keep/cut 状态"""
    index: int


@dataclass(frozen=True)
class BatchToggleAction:
    """批量切换多个 index 的 keep/cut 状态（F4 通道）"""
    indices: list[int]


@dataclass(frozen=True)
class FeedbackAction:
    """结构化反馈"""
    feedback_type: FeedbackType
    payload: dict[str, Any]
    # F1: {"text": str}
    # F2: {"index": int, "old": str, "new": str}
    # F3: {"text": str}
    # F4: {"indices": list[int]}  — 等价于 BatchToggleAction


@dataclass(frozen=True)
class AcceptAction:
    """确认当前决策，流转 Layer 3"""
    pass


@dataclass(frozen=True)
class QuitAction:
    """退出不保存"""
    pass


@dataclass(frozen=True)
class ShowAction:
    """刷新显示（不修改状态）"""
    pass


Action = ToggleAction | BatchToggleAction | FeedbackAction | AcceptAction | QuitAction | ShowAction


# ============================================================================
# Core API 返回值
# ============================================================================

@dataclass
class CoreResult:
    """run_2d 的返回值"""
    manifest_dict: dict[str, Any]
    signal: Signal
    display_data: DisplayData | None = None  # 供 Shell 渲染的结构化数据
    message: str = ""                         # 操作结果消息


@dataclass
class DisplayData:
    """供前端渲染的结构化展示数据"""
    tokens: list[dict]
    effective_mask: list[dict]
    overrides: list[dict]
    comprehension: dict
    review_report: dict
    goal: str
    feedback_history: list[dict]
    stats: dict  # {"keep_count": int, "cut_count": int, "total": int, "override_count": int}


# ============================================================================
# 合法的 feedback_type 值（6 种）
# ============================================================================

_LEGAL_FEEDBACK_TYPES = frozenset({
    "f1_purpose_drift",
    "f2_keyword_error",
    "f3_selection_opinion",
    "f4_time_point",
    "toggle",
    "confirm",
})


# ============================================================================
# 内部辅助函数
# ============================================================================

def _merge_keep_mask(
    keep_mask: list[dict],
    overrides: list[dict],
) -> list[dict]:
    """合并 overrides 到 keep_mask，返回新列表。

    对同一 index 多条 override 以最后一条为准。
    不修改原始 keep_mask。
    返回列表长度等于 keep_mask 长度。
    """
    result = [{"index": e["index"], "keep": e["keep"]} for e in keep_mask]

    for override in overrides:
        idx = override["index"]
        result[idx]["keep"] = override["keep"]

    return result


def _apply_toggle(
    keep_mask: list[dict],
    overrides: list[dict],
    index: int,
) -> list[dict]:
    """计算 effective mask，翻转指定 index 的 keep 状态，追加 delta 到 overrides。

    返回新的 overrides 列表（追加了一条 delta）。
    """
    effective_mask = _merge_keep_mask(keep_mask, overrides)
    current_keep = effective_mask[index]["keep"]
    new_keep = not current_keep

    # 返回新列表，不修改原 overrides
    new_overrides = list(overrides)
    new_overrides.append({"index": index, "keep": new_keep})

    logger.info(
        "index %s 已切换为 %s",
        index,
        "[保留]" if new_keep else "[删除]",
    )
    return new_overrides


def _build_display_data(
    manifest_dict: dict[str, Any],
    overrides: list[dict],
) -> DisplayData:
    """生成 DisplayData 结构化展示数据，含 stats 统计。"""
    tokens = manifest_dict["tokens"]
    keep_mask = manifest_dict["keep_mask"]
    effective_mask = _merge_keep_mask(keep_mask, overrides)

    keep_count = sum(1 for e in effective_mask if e["keep"] is True)
    cut_count = sum(1 for e in effective_mask if e["keep"] is False)
    total = len(tokens)

    # 统计有多少个 index 被 override 过（去重，取最终状态与原始不同的）
    override_indices = set()
    for o in overrides:
        override_indices.add(o["index"])
    override_count = len(override_indices)

    return DisplayData(
        tokens=tokens,
        effective_mask=effective_mask,
        overrides=list(overrides),
        comprehension=manifest_dict.get("comprehension", {}),
        review_report=manifest_dict.get("review_report", {}),
        goal=manifest_dict.get("goal", ""),
        feedback_history=manifest_dict.get("human_feedback_history", []),
        stats={
            "keep_count": keep_count,
            "cut_count": cut_count,
            "total": total,
            "override_count": override_count,
        },
    )


def _record_feedback_history(
    manifest_dict: dict[str, Any],
    feedback_type: str,
    payload: dict[str, Any],
    overrides: list[dict],
) -> None:
    """验证 feedback_type 并追加反馈记录到 human_feedback_history。

    feedback_type 必须为六种合法值之一：
    f1_purpose_drift, f2_keyword_error, f3_selection_opinion,
    f4_time_point, toggle, confirm
    """
    if feedback_type not in _LEGAL_FEEDBACK_TYPES:
        raise ValueError(
            f"非法 feedback_type: {feedback_type!r}，"
            f"合法值: {sorted(_LEGAL_FEEDBACK_TYPES)}"
        )

    history = manifest_dict.setdefault("human_feedback_history", [])
    current_round = len(history)

    record = {
        "round": current_round,
        "feedback_type": feedback_type,
        "feedback_payload": payload,
        "overrides": list(overrides),
        "timestamp": datetime.now().isoformat(),
    }
    history.append(record)


def _handle_feedback(
    manifest_dict: dict[str, Any],
    action: FeedbackAction,
    overrides: list[dict],
) -> CoreResult:
    """处理结构化反馈。

    路由逻辑：
        F1 (主旨偏差)     → 记录 + signal=REFLOW_2A
        F2 (关键词错误)   → 验证 + 记录 + signal=REFLOW_2A
        F3 (内容选择意见) → 记录 + signal=REFLOW_2B
        F4 (时间节点意见) → 等价于 BatchToggle + signal=CONTINUE
    """
    ft = action.feedback_type
    tokens = manifest_dict["tokens"]
    keep_mask = manifest_dict["keep_mask"]
    n = len(tokens)

    # --- F2 验证：index 范围 + old 子串存在性 ---
    if ft == FeedbackType.F2_KEYWORD_ERROR:
        idx = action.payload.get("index", -1)
        if idx < 0 or idx >= n:
            display = _build_display_data(manifest_dict, overrides)
            return CoreResult(
                manifest_dict, Signal.CONTINUE, display,
                f"F2: index {idx} 超出范围 [0, {n})",
            )
        old_str = action.payload.get("old", "")
        token_text = tokens[idx].get("text", "")
        if old_str not in token_text:
            display = _build_display_data(manifest_dict, overrides)
            return CoreResult(
                manifest_dict, Signal.CONTINUE, display,
                f"F2: 子串 {old_str!r} 在 tokens[{idx}].text 中找不到",
            )

    # --- F4：不触发 LLM 回流，等价于批量 toggle ---
    if ft == FeedbackType.F4_TIME_POINT:
        indices = action.payload.get("indices", [])
        # 验证 indices 范围
        for idx in indices:
            if idx < 0 or idx >= n:
                display = _build_display_data(manifest_dict, overrides)
                return CoreResult(
                    manifest_dict, Signal.CONTINUE, display,
                    f"F4: index {idx} 超出范围 [0, {n})",
                )
        for idx in indices:
            overrides = _apply_toggle(keep_mask, overrides, idx)
        manifest_dict["_2d_overrides"] = overrides
        _record_feedback_history(manifest_dict, ft.value, action.payload, overrides)
        display = _build_display_data(manifest_dict, overrides)
        return CoreResult(
            manifest_dict, Signal.CONTINUE, display,
            f"F4: 已切换 {len(indices)} 个时间节点",
        )

    # --- F1/F2/F3: 记录反馈并触发回流 ---
    _record_feedback_history(manifest_dict, ft.value, action.payload, overrides)

    # 注入回流上下文供编排器读取
    manifest_dict["_reflow_context"] = {
        "feedback_type": ft.value,
        "feedback_payload": action.payload,
    }

    if ft in (FeedbackType.F1_PURPOSE_DRIFT, FeedbackType.F2_KEYWORD_ERROR):
        signal = Signal.REFLOW_2A
        msg = f"{ft.value}: 将回流至 2a 重跑"
    else:  # F3_SELECTION_OPINION
        signal = Signal.REFLOW_2B
        msg = f"{ft.value}: 将回流至 2b 重跑"

    return CoreResult(manifest_dict, signal, message=msg)


# ============================================================================
# Core 主入口
# ============================================================================

def run_2d(manifest_dict: dict[str, Any], action: Action) -> CoreResult:
    """2d Core API 主入口：纯函数，接收 manifest + action，返回更新后的 manifest + 信号。

    Preconditions:
        - manifest_dict 包含 tokens、keep_mask（与 tokens 等长对齐）
        - manifest_dict 包含 comprehension（2a 产物）
        - action 是合法的 Action 类型实例

    Postconditions:
        - 返回的 manifest_dict 中 keep_mask 与 tokens 等长对齐
        - signal 为 Signal 枚举值之一
        - CONTINUE 信号时 manifest_dict 已应用 action 的变更
        - REFLOW_2A/REFLOW_2B 信号时 feedback_history 已追加本次反馈
        - DONE 信号时 keep_mask 已合并所有 overrides
    """
    tokens = manifest_dict["tokens"]
    keep_mask = manifest_dict["keep_mask"]
    overrides = manifest_dict.get("_2d_overrides", [])
    n = len(tokens)

    if isinstance(action, ToggleAction):
        # 前置条件：0 <= action.index < len(tokens)
        if action.index < 0 or action.index >= n:
            display = _build_display_data(manifest_dict, overrides)
            return CoreResult(
                manifest_dict, Signal.CONTINUE, display,
                f"index {action.index} 超出范围 [0, {n})",
            )
        overrides = _apply_toggle(keep_mask, overrides, action.index)
        manifest_dict["_2d_overrides"] = overrides
        display = _build_display_data(manifest_dict, overrides)
        return CoreResult(
            manifest_dict, Signal.CONTINUE, display,
            f"index {action.index} 已切换",
        )

    elif isinstance(action, BatchToggleAction):
        # 前置条件：所有 index 在 [0, len(tokens)) 范围内
        for idx in action.indices:
            if idx < 0 or idx >= n:
                display = _build_display_data(manifest_dict, overrides)
                return CoreResult(
                    manifest_dict, Signal.CONTINUE, display,
                    f"index {idx} 超出范围 [0, {n})",
                )
        for idx in action.indices:
            overrides = _apply_toggle(keep_mask, overrides, idx)
        manifest_dict["_2d_overrides"] = overrides
        display = _build_display_data(manifest_dict, overrides)
        return CoreResult(
            manifest_dict, Signal.CONTINUE, display,
            f"已切换 {len(action.indices)} 个 index",
        )

    elif isinstance(action, FeedbackAction):
        return _handle_feedback(manifest_dict, action, overrides)

    elif isinstance(action, AcceptAction):
        # 后置条件：keep_mask = merge(原始 keep_mask, 所有 overrides)
        final_mask = _merge_keep_mask(keep_mask, overrides)
        manifest_dict["keep_mask"] = final_mask
        _record_feedback_history(manifest_dict, "confirm", {}, overrides)
        # 清理临时字段
        manifest_dict.pop("_2d_overrides", None)
        return CoreResult(manifest_dict, Signal.DONE, message="确认完成")

    elif isinstance(action, QuitAction):
        return CoreResult(manifest_dict, Signal.QUIT, message="用户取消")

    elif isinstance(action, ShowAction):
        display = _build_display_data(manifest_dict, overrides)
        return CoreResult(manifest_dict, Signal.CONTINUE, display)

    else:
        # 未知 Action 类型
        return CoreResult(
            manifest_dict, Signal.CONTINUE,
            message=f"未知 Action 类型: {type(action).__name__}",
        )
