"""2b TUI 视图模型：纯 Python，无 Textual 依赖。

消费 StreamChunk 语义（通过 payload 字典传入亦可），维护块状态与增量解析的 decisions。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autosmartcut.nodes.l2.intelligence_llm import StreamChunk

logger = logging.getLogger(__name__)


@dataclass
class BlockState:
    block_ordinal: int
    stage: str  # decision_r1 / decision_r2
    status: str = "pending"
    thinking_text: str = ""
    thinking_done: bool = False
    thinking_token_est: int = 0  # 与方案中的 thinking_token_count 同义（估算）
    decisions: list[dict] = field(default_factory=list)
    content_raw: str = ""
    input_summary: str = ""
    thinking_expanded: bool = True

    @property
    def thinking_token_count(self) -> int:
        """与方案字段名一致；当前为字符量的一半估算。"""
        return self.thinking_token_est


class BlockDecisionParser:
    """从 content_delta 增量解析 ``decisions`` JSON 数组中的对象。"""

    def __init__(self, *, has_reason: bool) -> None:
        self._buf = ""
        self._has_reason = has_reason

    def feed(self, delta: str) -> list[dict]:
        self._buf += delta
        out: list[dict] = []
        while True:
            m = re.search(r"\{[^{}]*\}", self._buf)
            if not m:
                break
            raw = m.group(0)
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict) and "index" in obj and "keep" in obj:
                    out.append(obj)
            except json.JSONDecodeError:
                break
            self._buf = self._buf[m.end() :].lstrip(", \n\r\t")
        return out


class TwoBViewModel:
    """聚合多块的展示状态；由 UI 在收到 ``2b_chunk`` 时调用 ``apply_chunk``。"""

    def __init__(
        self,
        *,
        show_thinking_default: bool = True,
    ) -> None:
        self._blocks: dict[int, BlockState] = {}
        self._parsers: dict[int, BlockDecisionParser] = {}
        self._phase: str = "r1"
        self._active: int = 1
        self._show_thinking_default = show_thinking_default

    def ensure_block(
        self,
        ordinal: int,
        *,
        stage: str = "decision_r1",
        input_summary: str = "",
    ) -> None:
        if ordinal not in self._blocks:
            self._blocks[ordinal] = BlockState(
                block_ordinal=ordinal,
                stage=stage,
                input_summary=input_summary,
                thinking_expanded=self._show_thinking_default,
            )
            has_r = stage == "decision_r1"
            self._parsers[ordinal] = BlockDecisionParser(has_reason=has_r)

    def apply_chunk(self, block_ordinal: int, chunk: "StreamChunk") -> None:
        from autosmartcut.nodes.l2.intelligence_llm import StreamChunk as SC

        if not isinstance(chunk, SC):
            return
        st = chunk.stage
        if st == "decision_r2":
            self._phase = "r2"
        elif st == "decision_r1":
            self._phase = "r1"

        self.ensure_block(block_ordinal, stage=st)
        bs = self._blocks[block_ordinal]
        bs.stage = st

        if chunk.event == "retry":
            bs.status = "streaming"
            bs.thinking_text = ""
            bs.content_raw = ""
            bs.thinking_done = False
            return

        if chunk.event == "reasoning_delta" and chunk.reasoning_delta:
            bs.status = "streaming"
            bs.thinking_text += chunk.reasoning_delta
            bs.thinking_token_est = len(bs.thinking_text) // 2

        if chunk.event == "content_delta" and chunk.content_delta:
            if not bs.thinking_done and bs.thinking_text:
                bs.thinking_done = True
            bs.status = "streaming"
            bs.content_raw += chunk.content_delta
            parser = self._parsers.get(block_ordinal)
            if parser:
                new_rows = parser.feed(chunk.content_delta)
                if new_rows:
                    bs.decisions.extend(new_rows)

        if chunk.event == "result":
            bs.status = "done"
            bs.thinking_done = True

    def get_block(self, ordinal: int) -> BlockState | None:
        return self._blocks.get(ordinal)

    def update_chunk(self, block_ordinal: int, chunk: "StreamChunk") -> None:
        """与 ``apply_chunk`` 同义（方案命名）。"""
        self.apply_chunk(block_ordinal, chunk)

    def get_block_state(self, ordinal: int) -> BlockState | None:
        """与 ``get_block`` 同义（方案命名）。"""
        return self.get_block(ordinal)

    def list_ordinals(self) -> list[int]:
        return sorted(self._blocks.keys())

    def get_display_ordinals(self) -> list[int]:
        """当前阶段用于进度条展示的块序号（R1 与 R2 分区展示）。"""
        if self._phase == "r1":
            xs = sorted(
                k for k, b in self._blocks.items() if b.stage == "decision_r1"
            )
            return xs
        if self._phase in ("r2", "done"):
            xs = sorted(
                k for k, b in self._blocks.items() if b.stage == "decision_r2"
            )
            return xs
        return sorted(self._blocks.keys())

    def get_all_statuses(self) -> dict[int, str]:
        return {k: v.status for k, v in self._blocks.items()}

    def get_phase(self) -> str:
        return self._phase

    def get_active_block(self) -> int:
        return self._active

    def switch_to(self, ordinal: int) -> None:
        self._active = ordinal

    def toggle_thinking(self, ordinal: int) -> None:
        b = self._blocks.get(ordinal)
        if b:
            b.thinking_expanded = not b.thinking_expanded

    def mark_done(self) -> None:
        self._phase = "done"
        for b in self._blocks.values():
            if b.status == "streaming":
                b.status = "done"
