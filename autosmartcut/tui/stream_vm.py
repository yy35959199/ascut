"""tui/stream_vm.py — LLM 流式输出 ViewModel 层。

纯 Python，无 Textual 依赖，可独立单测。

设计原则：
- 以 slot_id 为 key 管理多个并发/串行 LLM 调用的状态
- 行模型：buffer 按 \\n 切分为已冻结行 + 当前不完整行
- 增量 flush：只返回自上次 flush 以来新增的行，避免全量重写
- 插件数据：addon_data 存储插件专用数据（如 decisions 列表），ViewModel 不解释语义
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class SlotState:
    """单个 LLM 调用的完整状态。"""

    slot_id: str
    label: str
    status: Literal["idle", "streaming", "done", "failed", "retrying"] = "idle"

    # reasoning 区（行模型）
    reasoning_lines: list[str] = field(default_factory=list)
    reasoning_cur: str = ""          # 当前不完整行（无 \\n 结尾）
    reasoning_done: bool = False

    # content 区（行模型）
    content_lines: list[str] = field(default_factory=list)
    content_cur: str = ""

    # flush 追踪：上次 flush 后各区已输出到第几行
    _r_flushed_to: int = 0
    _c_flushed_to: int = 0

    # 元数据
    attempt: int = 0
    retry_reason: str = ""

    # 附加数据（插件使用，ViewModel 不解释语义）
    addon_data: dict = field(default_factory=dict)
    _addon_flushed_to: int = 0       # 插件增量追踪（如 decisions 列表长度）


class LLMStreamViewModel:
    """管理多个 StreamSlot 的状态。纯 Python，与 UI 框架无关。

    典型用法：
        vm = LLMStreamViewModel()
        vm.register_slots([("r1", "R1 粗理解"), ("r2", "R2 精化")])

        # 收到 delta 时
        vm.feed_delta("r1", reasoning="思考中...")
        vm.feed_delta("r1", content="{")

        # 50ms 节流 flush（只返回新增行）
        new_r, r_cur, new_c, c_cur = vm.flush("r1")

        # 切换 slot 时全量重绘
        r_lines, r_cur, c_lines, c_cur = vm.flush_full("r2")
    """

    def __init__(self) -> None:
        self._slots: dict[str, SlotState] = {}
        self._order: list[str] = []
        self._active: str = ""

    # ── 生命周期 ──────────────────────────────────────────────────────────

    def reset(self) -> None:
        """清空所有 slot，回到初始状态。"""
        self._slots.clear()
        self._order.clear()
        self._active = ""

    def register_slot(self, slot_id: str, label: str) -> None:
        """注册一个 slot。首个注册的自动成为 active。"""
        if slot_id in self._slots:
            return
        self._slots[slot_id] = SlotState(slot_id=slot_id, label=label)
        self._order.append(slot_id)
        if not self._active:
            self._active = slot_id

    def register_slots(self, slots: list[tuple[str, str]]) -> None:
        """批量注册 slots。"""
        for sid, label in slots:
            self.register_slot(sid, label)

    # ── 数据输入 ──────────────────────────────────────────────────────────

    def feed_delta(self, slot_id: str, *, reasoning: str = "", content: str = "") -> None:
        """追加 delta 到 buffer，按 \\n 切分为完整行 + 当前行。

        slot_id 不存在时自动注册（容错）。
        自动将 status 从 idle 切为 streaming。
        """
        if slot_id not in self._slots:
            self.register_slot(slot_id, slot_id)
        slot = self._slots[slot_id]
        if slot.status == "idle":
            slot.status = "streaming"

        if reasoning:
            slot.reasoning_cur += reasoning
            *lines, slot.reasoning_cur = slot.reasoning_cur.split("\n")
            slot.reasoning_lines.extend(lines)

        if content:
            slot.content_cur += content
            *lines, slot.content_cur = slot.content_cur.split("\n")
            slot.content_lines.extend(lines)

    def mark_retry(self, slot_id: str, attempt: int, reason: str) -> None:
        """清空该 slot 的所有文本，status → retrying。"""
        if slot_id not in self._slots:
            return
        slot = self._slots[slot_id]
        slot.reasoning_lines.clear()
        slot.reasoning_cur = ""
        slot.content_lines.clear()
        slot.content_cur = ""
        slot._r_flushed_to = 0
        slot._c_flushed_to = 0
        slot.attempt = attempt
        slot.retry_reason = reason
        slot.status = "retrying"

    def mark_done(self, slot_id: str) -> None:
        """冻结 cur 为最后一行，status → done。

        若下一个 slot 是 idle，自动切换 active。
        """
        if slot_id not in self._slots:
            return
        slot = self._slots[slot_id]
        if slot.reasoning_cur:
            slot.reasoning_lines.append(slot.reasoning_cur)
            slot.reasoning_cur = ""
        if slot.content_cur:
            slot.content_lines.append(slot.content_cur)
            slot.content_cur = ""
        slot.reasoning_done = True
        slot.status = "done"
        self._auto_advance(slot_id)

    def mark_failed(self, slot_id: str, reason: str = "") -> None:
        """status → failed。"""
        if slot_id not in self._slots:
            return
        self._slots[slot_id].status = "failed"
        self._slots[slot_id].retry_reason = reason

    def set_addon_data(self, slot_id: str, key: str, value: object) -> None:
        """设置附加数据（如 decisions 列表）。ViewModel 不解释语义。"""
        if slot_id not in self._slots:
            return
        self._slots[slot_id].addon_data[key] = value

    # ── 查询 ──────────────────────────────────────────────────────────────

    def get_active(self) -> str:
        """当前 active slot_id。"""
        return self._active

    def get_slot(self, slot_id: str) -> SlotState | None:
        return self._slots.get(slot_id)

    def list_slots(self) -> list[str]:
        return list(self._order)

    def get_progress(self) -> list[tuple[str, str, str]]:
        """返回 [(slot_id, label, status), ...] 供进度条渲染。"""
        return [
            (sid, self._slots[sid].label, self._slots[sid].status)
            for sid in self._order
        ]

    # ── 导航 ──────────────────────────────────────────────────────────────

    def switch_to(self, slot_id: str) -> None:
        if slot_id in self._slots:
            self._active = slot_id

    def next_slot(self) -> None:
        self._step(1)

    def prev_slot(self) -> None:
        self._step(-1)

    def _step(self, delta: int) -> None:
        if not self._order:
            return
        try:
            i = self._order.index(self._active)
        except ValueError:
            i = 0
        self._active = self._order[(i + delta) % len(self._order)]

    def _auto_advance(self, done_slot_id: str) -> None:
        """done_slot 完成后，若下一个 slot 是 idle，自动切换 active。"""
        try:
            i = self._order.index(done_slot_id)
        except ValueError:
            return
        if i + 1 < len(self._order):
            next_id = self._order[i + 1]
            if self._slots[next_id].status == "idle":
                self._active = next_id

    # ── flush（增量读取）──────────────────────────────────────────────────

    def flush(self, slot_id: str) -> tuple[list[str], str, list[str], str]:
        """返回自上次 flush 以来新增的完整行 + 当前不完整行。

        同时更新 flushed_to 指针，避免重复输出。

        Returns:
            (new_reasoning_lines, reasoning_cur, new_content_lines, content_cur)
        """
        if slot_id not in self._slots:
            return [], "", [], ""
        slot = self._slots[slot_id]
        new_r = slot.reasoning_lines[slot._r_flushed_to:]
        new_c = slot.content_lines[slot._c_flushed_to:]
        slot._r_flushed_to = len(slot.reasoning_lines)
        slot._c_flushed_to = len(slot.content_lines)
        return new_r, slot.reasoning_cur, new_c, slot.content_cur

    def flush_full(self, slot_id: str) -> tuple[list[str], str, list[str], str]:
        """返回该 slot 的全部行（切换 slot 时全量重绘用）。

        同时将 flushed_to 指针推到末尾，避免后续 flush 重复输出。

        Returns:
            (all_reasoning_lines, reasoning_cur, all_content_lines, content_cur)
        """
        if slot_id not in self._slots:
            return [], "", [], ""
        slot = self._slots[slot_id]
        slot._r_flushed_to = len(slot.reasoning_lines)
        slot._c_flushed_to = len(slot.content_lines)
        return (
            list(slot.reasoning_lines),
            slot.reasoning_cur,
            list(slot.content_lines),
            slot.content_cur,
        )

    def flush_addon(self, slot_id: str, key: str) -> tuple[list, int]:
        """返回 addon_data[key] 中自上次 flush 以来新增的条目。

        Returns:
            (new_items, total_count)
        """
        if slot_id not in self._slots:
            return [], 0
        slot = self._slots[slot_id]
        items = slot.addon_data.get(key, [])
        if not isinstance(items, list):
            return [], 0
        new_items = items[slot._addon_flushed_to:]
        slot._addon_flushed_to = len(items)
        return new_items, len(items)
