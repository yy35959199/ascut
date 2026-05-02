"""tui/addons.py — LLMStreamView 插件 Widget。

插件挂载到 LLMStreamView 的 #stream-addon 容器，提供节点专用的附加面板。

包含：
- DecisionsAddon   2b 流式阶段的决策列表（增量追加，不全量重写）
- ResultAddon      2b 完成后的保留/删除总览（DataTable 虚拟化）

插件协议（非正式）：
    refresh_from_slot(slot: SlotState) -> None   全量重绘（slot 切换时）
    append_items(items: list) -> None            增量追加（50ms flush 时）
    clear() -> None                              节点结束或 reset 时
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autosmartcut.tui.stream_vm import SlotState

logger = logging.getLogger(__name__)

try:
    from textual.widget import Widget
    from textual.widgets import DataTable, RichLog, Static

    _TEXTUAL_AVAILABLE = True
except ImportError:
    _TEXTUAL_AVAILABLE = False


if _TEXTUAL_AVAILABLE:

    # -----------------------------------------------------------------------
    # DecisionsAddon — 2b 流式阶段决策列表
    # -----------------------------------------------------------------------

    class DecisionsAddon(Widget):
        """2b 决策列表面板：增量追加，不全量重写。

        slot 切换时调用 refresh_from_slot 全量重绘；
        50ms flush 时调用 append_items 增量追加新决策。
        """

        def compose(self):
            yield Static("[bold]决策流[/bold]", id="addon-decisions-label")
            yield RichLog(
                id="addon-decisions-log",
                max_lines=1200,
                wrap=True,
                auto_scroll=True,
            )

        def refresh_from_slot(self, slot: "SlotState") -> None:
            """全量重绘（slot 切换时调用）。"""
            decisions = slot.addon_data.get("decisions", [])
            try:
                rl = self.query_one("#addon-decisions-log", RichLog)
                rl.clear()
                for d in decisions:
                    rl.write(self._fmt(d))
            except Exception:
                pass

        def append_items(self, items: list) -> None:
            """增量追加新决策（50ms flush 时调用）。"""
            if not items:
                return
            try:
                rl = self.query_one("#addon-decisions-log", RichLog)
                for d in items:
                    rl.write(self._fmt(d))
            except Exception:
                pass

        def clear(self) -> None:
            try:
                self.query_one("#addon-decisions-log", RichLog).clear()
            except Exception:
                pass

        @staticmethod
        def _fmt(d: dict) -> str:
            from autosmartcut.nodes.l2.intelligence_2b import REASON_LABELS
            idx = d.get("index", "?")
            keep = d.get("keep")
            reason = str(d.get("reason", "ok"))
            tag = REASON_LABELS.get(reason, "✓" if keep else "✗")
            return f"  [{idx}] {tag}  keep={keep}"

    # -----------------------------------------------------------------------
    # ResultAddon — 2b 完成后保留/删除总览
    # -----------------------------------------------------------------------

    class ResultAddon(Widget):
        """2b 完成后的保留/删除总览，DataTable 虚拟化渲染。

        DataTable 只渲染可见行，resize 时不全量 re-wrap，性能远优于 Static。
        """

        def compose(self):
            yield Static("", id="addon-result-header")
            yield DataTable(
                id="addon-result-table",
                cursor_type="row",
                zebra_stripes=True,
            )

        def show_result(
            self,
            tokens: list[dict],
            keep_mask: list[dict],
            comprehension: dict,
        ) -> None:
            """填充 DataTable（一次性调用）。"""
            kc = sum(1 for e in keep_mask if e.get("keep"))
            cc = len(keep_mask) - kc
            try:
                self.query_one("#addon-result-header", Static).update(
                    f"[bold]2b 结果：{kc} 保留 / {cc} 删除 / 共 {len(keep_mask)}[/bold]"
                )
                table = self.query_one("#addon-result-table", DataTable)
                table.clear(columns=True)
                table.add_columns("状态", "序号", "内容")
                for i, tok in enumerate(tokens):
                    keep = keep_mask[i]["keep"] if i < len(keep_mask) else True
                    table.add_row(
                        "[yellow]保留[/yellow]" if keep else "删除",
                        str(i),
                        tok.get("text", "")[:60],
                    )
            except Exception as e:
                logger.warning("ResultAddon.show_result 失败: %s", e)

        def refresh_from_slot(self, slot: "SlotState") -> None:
            """ResultAddon 由 show_result 一次性填充，slot 切换时无需刷新。"""
            pass

        def append_items(self, items: list) -> None:
            pass

        def clear(self) -> None:
            try:
                self.query_one("#addon-result-table", DataTable).clear()
            except Exception:
                pass

else:
    class DecisionsAddon:  # type: ignore[no-redef]
        pass

    class ResultAddon:  # type: ignore[no-redef]
        pass
