"""tui/widgets.py — Textual Widget 组件。

包含：
- PipelineSidebar  侧边栏（节点状态）
- GenericStageView 通用阶段视图
- L1aProgressView  L1A 专用进度视图
- MainArea         主区域（切换视图）
- LogArea          日志区域
- ReviewScreen     L2D 人工审阅界面（Widget，嵌入 MainArea）
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from autosmartcut.nodes.l2.intelligence_2d_core import DisplayData
    from autosmartcut.pipeline.pipeline_events import PipelineEvent

logger = logging.getLogger(__name__)

try:
    from textual.containers import VerticalScroll
    from textual.screen import Screen
    from textual.widget import Widget
    from textual.widgets import Button, Input, RichLog, Static

    _TEXTUAL_AVAILABLE = True
except ImportError:
    _TEXTUAL_AVAILABLE = False


if _TEXTUAL_AVAILABLE:
    import time

    from textual.binding import Binding
    from textual.containers import Vertical, VerticalScroll

    from autosmartcut.cli.formatters import (
        format_decision_list,
        format_progress,
        format_review_summary,
        format_stats,
        parse_l1a_chunk_done,
        parse_l1a_intra_chunk_progress,
    )

    # -----------------------------------------------------------------------
    # PipelineSidebar
    # -----------------------------------------------------------------------

    class PipelineSidebar(Widget):
        """侧边栏：显示各节点的运行状态。"""

        _NODE_LABELS: dict[str, str] = {
            "l1_perception":     "L1 识别与对齐",
            "l2a_comprehension": "L2A 理解",
            "l2b_decision":      "L2B 决策",
            "l2c_review":        "L2C 审核",
            "l2d_human":         "L2D 人工",
            "l3_execute":        "L3 执行",
        }
        _STATUS_ICONS: dict[str, str] = {
            "pending":   "○",
            "running":   "→",
            "completed": "✓",
            "failed":    "✗",
            "skipped":   "⊘",
        }

        def compose(self):
            yield Static("流水线进度", classes="sidebar-title")
            for node_id, label in self._NODE_LABELS.items():
                yield Static(
                    f"○ {label}",
                    id=f"node-{node_id}",
                    classes="node-pending",
                )

        def update_node_status(self, node_id: str, status: str) -> None:
            label = self._NODE_LABELS.get(node_id, node_id)
            icon = self._STATUS_ICONS.get(status, "?")
            try:
                widget = self.query_one(f"#node-{node_id}", Static)
                widget.update(f"{icon} {label}")
                widget.remove_class(
                    "node-pending", "node-running",
                    "node-completed", "node-failed", "node-skipped",
                )
                widget.add_class(f"node-{status}")
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # LLMStreamView
    # -----------------------------------------------------------------------

    class LLMStreamView(Widget):
        """LLM 流式输出视图：双面板显示 reasoning 和 content。

        状态栏：当前 stage + 状态（思考中 / 生成中 / 重试中 / 完成）
        reasoning 面板：thinking 过程（暗色，可滚动）
        content 面板：最终 JSON 输出（主色调，高度自适应）

        生命周期：
        - ``reset()``：新 stage 开始时清空所有面板
        - ``handle_llm_chunk(payload)``：处理 ProgressEvent(phase="llm_stream") 的 payload
        """

        def compose(self):
            yield Static("", id="llm-status-bar")
            yield RichLog(id="llm-reasoning", max_lines=500, wrap=True, auto_scroll=True)
            yield Static("─" * 40, id="llm-divider")
            yield RichLog(id="llm-content", max_lines=200, wrap=True, auto_scroll=True)

        def handle_llm_chunk(self, payload: dict) -> None:
            """处理单个 llm_stream payload，更新对应面板。"""
            evt = payload.get("event", "")
            stage = payload.get("stage", "")

            match evt:
                case "reasoning_delta":
                    delta = payload.get("reasoning_delta", "")
                    if delta:
                        try:
                            self.query_one("#llm-status-bar", Static).update(
                                f"🧠 [{stage}] 思考中..."
                            )
                            self.query_one("#llm-reasoning", RichLog).write(delta)
                        except Exception:
                            pass

                case "content_delta":
                    delta = payload.get("content_delta", "")
                    if delta:
                        try:
                            self.query_one("#llm-status-bar", Static).update(
                                f"✍ [{stage}] 生成中..."
                            )
                            self.query_one("#llm-content", RichLog).write(delta)
                        except Exception:
                            pass

                case "retry":
                    attempt = payload.get("attempt", 0)
                    reason = payload.get("retry_reason", "")
                    reason_short = reason[:50] + "…" if len(reason) > 50 else reason
                    try:
                        self.query_one("#llm-status-bar", Static).update(
                            f"⟳ [{stage}] 重试（第 {attempt} 次）: {reason_short}"
                        )
                        # 清空面板，准备接收新一轮输出
                        self.query_one("#llm-reasoning", RichLog).clear()
                        self.query_one("#llm-content", RichLog).clear()
                    except Exception:
                        pass

                case "result":
                    try:
                        self.query_one("#llm-status-bar", Static).update(
                            f"✓ [{stage}] 完成"
                        )
                    except Exception:
                        pass

        def reset(self) -> None:
            """新 stage 开始时清空所有面板。"""
            try:
                self.query_one("#llm-status-bar", Static).update("")
                self.query_one("#llm-reasoning", RichLog).clear()
                self.query_one("#llm-content", RichLog).clear()
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # GenericStageView
    # -----------------------------------------------------------------------

    class GenericStageView(Widget):
        """通用阶段视图：RichLog 承载历史行，底部 Static 显示当前进度。"""

        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._current_text: str = ""

        def compose(self):
            yield RichLog(id="generic-log", max_lines=200, wrap=True, auto_scroll=True)
            yield Static("", id="generic-current")

        def append_frozen(self, line: str) -> None:
            if line:
                try:
                    self.query_one("#generic-log", RichLog).write(line)
                except Exception:
                    pass

        def set_current(self, line: str) -> None:
            self._current_text = line
            try:
                self.query_one("#generic-current", Static).update(line)
            except Exception:
                pass

        def freeze_current(self) -> None:
            if self._current_text:
                self.append_frozen(self._current_text)
            self.set_current("")

    # -----------------------------------------------------------------------
    # L1aProgressView
    # -----------------------------------------------------------------------

    class L1aProgressView(Widget):
        """L1A 专用进度视图：三区分离（进度条 / 识别文本 / 块状态）。"""

        def compose(self):
            yield Static("", id="l1a-progress-bar")
            yield RichLog(id="l1a-text-log", max_lines=200, wrap=True, auto_scroll=True)
            yield Static("", id="l1a-chunk-status")

        def on_mount(self) -> None:
            self._texts: list[str] = []

        def on_resize(self) -> None:
            self._redraw_text_log()

        def _redraw_text_log(self) -> None:
            try:
                from rich.text import Text
                log = self.query_one("#l1a-text-log", RichLog)
                log.clear()
                if not self._texts:
                    return
                for text in self._texts[:-1]:
                    log.write(text)
                # 使用 Rich Text 对象来正确应用自定义绿色样式 (128,255,181)
                latest_text = Text(self._texts[-1], style="rgb(128,255,181)")
                log.write(latest_text)
                log.scroll_end(animate=False)
            except Exception:
                pass

        def handle_progress(self, event: "PipelineEvent") -> None:
            phase = event.phase
            payload = event.payload

            if phase == "asr_intra_chunk_progress":
                s = parse_l1a_intra_chunk_progress(payload)
                bar_width = 20
                filled = int(s.pct / 100 * bar_width)
                bar = "█" * filled + "░" * (bar_width - filled)
                speed = f" {s.speed_str}" if s.speed_str else ""
                line = f"  [{s.chunk_id+1}/{s.total_chunks}] {bar} {s.pct:.0f}%  剩余 {s.remain_str}{speed}"
                try:
                    self.query_one("#l1a-progress-bar", Static).update(line)
                except Exception:
                    pass

            elif phase == "asr_chunk_done":
                s = parse_l1a_chunk_done(payload)
                if s.text:
                    self._texts.append(s.text)
                    self._redraw_text_log()
                try:
                    self.query_one("#l1a-chunk-status", Static).update("")
                except Exception:
                    pass

            else:
                status_line = format_progress(event.node_id, event.phase, event.payload)
                try:
                    self.query_one("#l1a-chunk-status", Static).update(status_line)
                except Exception:
                    pass

    # -----------------------------------------------------------------------
    # 2b：BlockProgressBar / BlockChatView / ResultView / TwoBStageView
    # -----------------------------------------------------------------------

    class BlockProgressBar(Static):
        """一排块状态指示器（○/⟳/✓/✗），由 TwoBStageView 驱动。"""

        def __init__(self, **kwargs) -> None:
            super().__init__("", **kwargs)

    class BlockChatView(Widget):
        """单块摘要 + 思考区 + 决策流。"""

        def compose(self) -> None:
            yield Static("", id="twob-chat-summary")
            yield Static("▼ 思考", id="twob-thinking-toggle")
            yield RichLog(id="twob-thinking-log", max_lines=400, wrap=True, auto_scroll=True)
            yield Static("[bold]决策流[/bold]", id="twob-decisions-label")
            yield RichLog(id="twob-decisions-log", max_lines=1200, wrap=True, auto_scroll=True)

    class ResultView(Widget):
        """与 2d 相同的保留/删除序列展示（依赖 format_decision_list）。"""

        def compose(self) -> None:
            with VerticalScroll():
                yield Static("", id="twob-result-body")

        def clear(self) -> None:
            try:
                self.query_one("#twob-result-body", Static).update("")
            except Exception:
                pass

        def show_result(
            self,
            *,
            tokens: list[dict],
            keep_mask: list[dict],
            comprehension: dict,
        ) -> None:
            from autosmartcut.nodes.l2.intelligence_2d_core import DisplayData

            kc = sum(1 for e in keep_mask if e.get("keep") is True)
            cc = sum(1 for e in keep_mask if e.get("keep") is False)
            total = len(keep_mask)
            stats = {
                "keep_count": kc,
                "cut_count": cc,
                "total": total,
                "override_count": 0,
            }
            dd = DisplayData(
                tokens=tokens,
                effective_mask=keep_mask,
                overrides=[],
                comprehension=comprehension,
                review_report={},
                goal="",
                feedback_history=[],
                stats=stats,
            )
            try:
                body = format_decision_list(dd, use_markup=True)
                head = (
                    f"[bold]2b 最终保留/删除（{kc} 保留 / {cc} 删除 / 共 {total}）[/bold]\n"
                )
                self.query_one("#twob-result-body", Static).update(head + body)
            except Exception as e:
                logger.warning("ResultView.show_result 失败: %s", e)

    class TwoBStageView(Widget):
        """2b 分块流式主容器：ViewModel + 子部件 + 约 50ms 刷新节流。"""

        can_focus = True

        BINDINGS = [
            Binding("left", "prev_block", "上一块", show=False),
            Binding("right", "next_block", "下一块", show=False),
            Binding("t", "toggle_thinking", "思考", show=False),
        ]

        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._vm: "TwoBViewModel | None" = None
            self._n_r1_est: int = 1
            self._last_ui_refresh: float = 0.0
            self._refresh_scheduled: bool = False
            self._debounce_until: float = 0.0

        def compose(self) -> None:
            yield Static("2b 决策（R1 → R2）", id="twob-header")
            yield BlockProgressBar(id="twob-progress")
            with Vertical(id="twob-middle"):
                yield BlockChatView(id="twob-chat")
            yield ResultView(id="twob-result")

        def reset(self) -> None:
            self._vm = None
            self._n_r1_est = 1
            self._last_ui_refresh = 0.0
            self._debounce_until = 0.0
            try:
                self.query_one("#twob-header", Static).update("2b 决策（R1 → R2）")
                self.query_one("#twob-progress", BlockProgressBar).update("")
                self.query_one("#twob-chat-summary", Static).update("")
                self.query_one("#twob-thinking-toggle", Static).update("▼ 思考")
                self.query_one("#twob-thinking-log", RichLog).clear()
                self.query_one("#twob-decisions-log", RichLog).clear()
                self.query_one("#twob-result", ResultView).display = False
                self.query_one("#twob-result", ResultView).clear()
            except Exception:
                pass

        def on_2b_start(self, payload: dict) -> None:
            from autosmartcut.nodes.l2.l2b_view_model import TwoBViewModel

            show_th = bool(payload.get("show_thinking_default", True))
            self._n_r1_est = max(1, int(payload.get("n_blocks_r1_estimate", 1)))
            self._vm = TwoBViewModel(show_thinking_default=show_th)
            self.reset_panels()
            self._refresh_ui_immediate()
            try:
                self.focus()
            except Exception:
                pass

        def reset_panels(self) -> None:
            try:
                self.query_one("#twob-chat-summary", Static).update(
                    f"R1 口语清洗 · 约 {self._n_r1_est} 块（流式到达后显示各块详情）"
                )
            except Exception:
                pass

        def _build_stream_chunk(self, payload: dict) -> object:
            from autosmartcut.nodes.l2.intelligence_llm import StreamChunk

            ev = payload.get("event", "content_delta")
            if ev not in (
                "reasoning_delta",
                "content_delta",
                "usage",
                "retry",
                "result",
            ):
                ev = "content_delta"
            return StreamChunk(
                stage=str(payload.get("stage", "")),
                event=ev,  # type: ignore[arg-type]
                reasoning_delta=str(payload.get("reasoning_delta") or ""),
                content_delta=str(payload.get("content_delta") or ""),
                attempt=int(payload.get("attempt") or 0),
                retry_reason=str(payload.get("retry_reason") or ""),
            )

        def handle_2b_chunk(self, payload: dict) -> None:
            if self._vm is None:
                return
            bo = int(payload.get("block_ordinal", 0))
            sc = self._build_stream_chunk(payload)
            self._vm.update_chunk(bo, sc)
            self._schedule_refresh()

        def _schedule_refresh(self) -> None:
            now = time.monotonic()
            if now - self._last_ui_refresh < 0.05:
                if not self._refresh_scheduled:
                    self._refresh_scheduled = True
                    self._debounce_until = now + 0.05
                    self.set_timer(0.05, self._flush_debounced_refresh)
                return
            self._last_ui_refresh = now
            self._refresh_ui_immediate()

        def _flush_debounced_refresh(self) -> None:
            self._refresh_scheduled = False
            now = time.monotonic()
            if now < self._debounce_until:
                # 仍可能更晚到达的 chunk
                self.set_timer(self._debounce_until - now, self._flush_debounced_refresh)
                return
            self._last_ui_refresh = now
            self._refresh_ui_immediate()

        def _format_progress_rich(self) -> str:
            if self._vm is None:
                return " ".join("○" for _ in range(self._n_r1_est))
            active = self._vm.get_active_block()
            ph = self._vm.get_phase()
            ordinals = self._vm.get_display_ordinals()
            if ph == "r1" and not ordinals:
                ordinals = list(range(1, self._n_r1_est + 1))
            if ph in ("r2", "done") and not ordinals:
                return "[dim]R2 启动中…（等待首包）[/dim]"

            parts: list[str] = []
            for o in ordinals:
                st = self._vm.get_block_state(o)
                if st is None:
                    sym, color = "○", ""
                else:
                    if st.status == "failed":
                        sym, color = "✗", "red"
                    elif st.status == "done":
                        sym, color = "✓", "green"
                    elif st.status == "streaming":
                        sym, color = "⟳", "dark_orange"
                    else:
                        sym, color = "○", "dim"
                is_active = o == active
                if is_active:
                    cell = f"[bold reverse]{sym}[/bold reverse]"
                elif color:
                    cell = f"[{color}]{sym}[/{color}]"
                else:
                    cell = sym
                parts.append(cell)
            label = "R1" if ph == "r1" else ("R2" if ph == "r2" else "完成")
            return f"[dim]{label}[/dim]  " + " ".join(parts) + f"  [dim]· 活动块 {active} · ←/→ 切换 · t 展开思考[/dim]"

        def _refresh_ui_immediate(self) -> None:
            if self._vm is None:
                return
            try:
                self.query_one("#twob-progress", BlockProgressBar).update(
                    self._format_progress_rich()
                )
            except Exception:
                pass
            self._render_chat_panels()

        def _render_chat_panels(self) -> None:
            if self._vm is None:
                return
            active = self._vm.get_active_block()
            st = self._vm.get_block_state(active)
            if st is None:
                ords = self._vm.list_ordinals()
                if ords:
                    self._vm.switch_to(ords[0])
                    st = self._vm.get_block_state(ords[0])
            if st is None:
                return
            ph = self._vm.get_phase()
            stage_label = "R1" if st.stage == "decision_r1" else "R2"
            summ = st.input_summary or f"{stage_label} · 块 {st.block_ordinal}"
            try:
                self.query_one("#twob-chat-summary", Static).update(
                    f"[bold]{summ}[/bold]  [dim]（{ph}）[/dim]"
                )
            except Exception:
                pass
            exp = st.thinking_expanded
            arrow = "▼" if exp else "▶"
            try:
                self.query_one("#twob-thinking-toggle", Static).update(
                    f"{arrow} 思考  "
                    f"{'（已完成）' if st.thinking_done else '（流式中…）'}  "
                    f"~{st.thinking_token_count} tok"
                )
            except Exception:
                pass
            try:
                th = self.query_one("#twob-thinking-log", RichLog)
                th.clear()
                if exp and st.thinking_text:
                    th.write(st.thinking_text)
            except Exception:
                pass
            from autosmartcut.nodes.l2.intelligence_2b import REASON_LABELS

            try:
                dec = self.query_one("#twob-decisions-log", RichLog)
                dec.clear()
                for d in st.decisions:
                    idx = d.get("index", "?")
                    keep = d.get("keep")
                    reason = str(d.get("reason", "ok"))
                    if "reason" in d and reason in REASON_LABELS:
                        tag = REASON_LABELS[reason]
                    else:
                        tag = "✓" if keep else "✗"
                    dec.write(f"  [{idx}] {tag}  keep={keep}")
            except Exception:
                pass

        def action_prev_block(self) -> None:
            self._step_active(-1)

        def action_next_block(self) -> None:
            self._step_active(1)

        def action_toggle_thinking(self) -> None:
            if self._vm is None:
                return
            self._vm.toggle_thinking(self._vm.get_active_block())
            self._refresh_ui_immediate()

        def _step_active(self, delta: int) -> None:
            if self._vm is None:
                return
            cur = self._ordinals_for_navigation()
            if not cur:
                return
            try:
                i = cur.index(self._vm.get_active_block())
            except ValueError:
                i = 0
            i = (i + delta) % len(cur)
            self._vm.switch_to(cur[i])
            self._refresh_ui_immediate()

        def _ordinals_for_navigation(self) -> list[int]:
            if self._vm is None:
                return []
            xs = self._vm.get_display_ordinals()
            if xs:
                return xs
            return self._vm.list_ordinals()

        def mark_done(self, payload: dict | None = None) -> None:
            if self._vm is not None:
                self._vm.mark_done()
            try:
                self.query_one("#twob-header", Static).update("2b 决策 · 已完成")
            except Exception:
                pass
            self._refresh_ui_immediate()
            if payload and payload.get("tokens") and payload.get("keep_mask") is not None:
                try:
                    rv = self.query_one("#twob-result", ResultView)
                    rv.show_result(
                        tokens=list(payload["tokens"]),
                        keep_mask=list(payload["keep_mask"]),
                        comprehension=dict(payload.get("comprehension") or {}),
                    )
                    rv.display = True
                except Exception as e:
                    logger.warning("TwoBStageView.mark_done 展示结果失败: %s", e)

    # -----------------------------------------------------------------------
    # MainArea
    # -----------------------------------------------------------------------

    class MainArea(Widget):
        """主区域：根据活跃节点切换视图。"""

        def compose(self):
            yield GenericStageView(id="generic-view")
            yield LLMStreamView(id="llm-stream-view")
            yield TwoBStageView(id="twob-stage")

        def show_stage_progress(self, node_id: str) -> None:
            if node_id == "l1_perception":
                self._switch_to_l1a_view()
            elif node_id.startswith("l2"):
                # L2 节点：切换到 LLM 流式视图
                self._switch_to_llm_stream_view()
            else:
                self._ensure_generic_view()
                try:
                    gv = self.query_one("#generic-view", GenericStageView)
                    gv.set_current(f"→ {node_id}")
                except Exception:
                    pass

        def show_stage_summary(
            self, node_id: str, summary: str, elapsed_sec: float = 0.0
        ) -> None:
            elapsed_str = f" ({elapsed_sec:.1f}s)" if elapsed_sec > 0 else ""
            summary_line = f"✓ {node_id}{elapsed_str}"
            if node_id == "l1_perception":
                self._teardown_l1a_view(summary_line)
            elif node_id.startswith("l2"):
                # L2 节点完成：隐藏流式视图与 2b 视图，切回 generic 并记录摘要
                try:
                    lv = self.query_one("#llm-stream-view", LLMStreamView)
                    lv.display = False
                except Exception:
                    pass
                try:
                    self.query_one("#twob-stage", TwoBStageView).display = False
                except Exception:
                    pass
                self._ensure_generic_view()
                try:
                    gv = self.query_one("#generic-view", GenericStageView)
                    gv.freeze_current()
                    gv.append_frozen(summary_line)
                    gv.set_current("")
                except Exception:
                    pass
            else:
                try:
                    gv = self.query_one("#generic-view", GenericStageView)
                    gv.freeze_current()
                    gv.append_frozen(summary_line)
                    gv.set_current("")
                except Exception:
                    pass

        def handle_node_progress(self, event: "PipelineEvent") -> None:
            if event.node_id == "l1_perception":
                try:
                    self.query_one("#l1a-view", L1aProgressView).handle_progress(event)
                except Exception:
                    pass
            elif event.phase == "llm_stream":
                # LLM 流式 chunk → LLMStreamView
                try:
                    self.query_one("#llm-stream-view", LLMStreamView).handle_llm_chunk(
                        event.payload
                    )
                except Exception:
                    pass
            elif event.phase == "2b_start":
                try:
                    tw = self.query_one("#twob-stage", TwoBStageView)
                    tw.reset()
                    tw.on_2b_start(event.payload or {})
                    tw.display = True
                    self.query_one("#llm-stream-view", LLMStreamView).display = False
                    self.query_one("#generic-view", GenericStageView).display = False
                except Exception:
                    pass
            elif event.phase == "2b_chunk":
                try:
                    self.query_one("#twob-stage", TwoBStageView).handle_2b_chunk(
                        event.payload
                    )
                except Exception:
                    pass
            elif event.phase == "2b_done":
                try:
                    self.query_one("#twob-stage", TwoBStageView).mark_done(
                        event.payload or {}
                    )
                except Exception:
                    pass
            else:
                # 其他 progress（decision_start, review_start 等）→ GenericStageView 状态栏
                try:
                    gv = self.query_one("#generic-view", GenericStageView)
                    gv.set_current(format_progress(event.node_id, event.phase, event.payload))
                except Exception:
                    pass

        def _switch_to_l1a_view(self) -> None:
            try:
                gv = self.query_one("#generic-view", GenericStageView)
                gv.display = False
            except Exception:
                pass
            existing = self.query("#l1a-view")
            if not existing:
                try:
                    self.mount(L1aProgressView(id="l1a-view"))
                except Exception:
                    pass

        def _switch_to_llm_stream_view(self) -> None:
            """切换到 LLM 流式视图，清空上次内容。"""
            try:
                self.query_one("#generic-view", GenericStageView).display = False
            except Exception:
                pass
            try:
                lv = self.query_one("#llm-stream-view", LLMStreamView)
                lv.display = True
                lv.reset()
            except Exception:
                pass

        def _teardown_l1a_view(self, summary_line: str) -> None:
            try:
                lv = self.query_one("#l1a-view", L1aProgressView)
                lv.remove()
            except Exception:
                pass
            self._ensure_generic_view()
            try:
                gv = self.query_one("#generic-view", GenericStageView)
                gv.append_frozen(summary_line)
            except Exception:
                pass

        def _ensure_generic_view(self) -> None:
            try:
                gv = self.query_one("#generic-view", GenericStageView)
                gv.display = True
            except Exception:
                pass

        def show_error(self, node_id: str, error: str) -> None:
            self._ensure_generic_view()
            try:
                gv = self.query_one("#generic-view", GenericStageView)
                gv.freeze_current()
                gv.append_frozen(f"[错误] {node_id}: {error}")
                gv.set_current("")
            except Exception:
                pass

        def show_complete(self, output: str) -> None:
            self._ensure_generic_view()
            try:
                gv = self.query_one("#generic-view", GenericStageView)
                gv.freeze_current()
                gv.append_frozen(f"✓ 完成 → {output}")
                gv.set_current("")
            except Exception:
                pass

        def show_review_screen(
            self,
            display: "DisplayData | None",
            on_action: Callable,
        ) -> None:
            """在主区域显示 ReviewScreen（2d 人工审阅界面）。"""
            try:
                for old in self.query(ReviewScreen):
                    old.remove()
                try:
                    self.query_one("#generic-view", GenericStageView).display = False
                except Exception:
                    pass
                review = ReviewScreen(display=display, on_action=on_action)
                self.mount(review)
            except Exception as e:
                logger.warning("show_review_screen 失败: %s", e)

    # -----------------------------------------------------------------------
    # LogArea
    # -----------------------------------------------------------------------

    class LogArea(Widget):
        """日志区域：可滚动，显示最近日志。"""

        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._log_screen_ref: "RichLog | None" = None

        def compose(self):
            yield RichLog(id="log-rich", max_lines=2000, wrap=True)

        def append_log(self, level: str, node_id: str, message: str) -> None:
            prefix = f"[{level}] " if level not in ("INFO", "") else ""
            node_prefix = f"[{node_id}] " if node_id else ""
            text = f"{prefix}{node_prefix}{message}"
            try:
                log_widget = self.query_one("#log-rich", RichLog)
                log_widget.write(text)
            except Exception:
                pass
            if self._log_screen_ref is not None:
                try:
                    self._log_screen_ref.write(text)
                except Exception:
                    self._log_screen_ref = None

    # -----------------------------------------------------------------------
    # ReviewScreen（Widget，嵌入 MainArea，用于 L2D 人工审阅）
    # -----------------------------------------------------------------------

    class ReviewScreen(Widget):
        """2d 人工审阅界面，嵌入主区域。"""

        def __init__(
            self,
            display: "DisplayData | None",
            on_action: Callable,
            **kwargs,
        ) -> None:
            super().__init__(**kwargs)
            self._display = display
            self._on_action = on_action

        def compose(self):
            with VerticalScroll():
                yield Static(id="goal-header")
                yield Static(id="review-summary")
                yield Static(id="decision-list")
            yield Static(id="stats-bar")
            yield Static(id="message-bar")
            yield Input(
                placeholder="命令: t/f1/f2/f3/f4/a/q/?",
                id="cmd-input",
            )

        def on_mount(self) -> None:
            self._refresh_display()
            try:
                self.query_one("#cmd-input", Input).focus()
            except Exception:
                pass

        def _refresh_display(self) -> None:
            dd = self._display
            if dd is None:
                return
            try:
                self.query_one("#goal-header", Static).update(
                    f"目标: {dd.goal}\n主旨: {dd.comprehension.get('purpose', '')}"
                )
                self.query_one("#review-summary", Static).update(
                    format_review_summary(dd.review_report)
                )
                self.query_one("#decision-list", Static).update(
                    format_decision_list(dd, use_markup=True)
                )
                self.query_one("#stats-bar", Static).update(
                    format_stats(dd.stats)
                )
            except Exception as e:
                logger.warning("ReviewScreen._refresh_display 失败: %s", e)

        def update_display(self, display: "DisplayData") -> None:
            self._display = display
            self._refresh_display()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            from autosmartcut.nodes.l2.intelligence_2d_shell import HELP_TEXT, parse_command

            raw = event.value.strip()
            try:
                self.query_one("#cmd-input", Input).value = ""
            except Exception:
                pass
            if not raw:
                return

            parsed = parse_command(raw)

            if parsed == "show_help":
                try:
                    self.query_one("#message-bar", Static).update(HELP_TEXT)
                except Exception:
                    pass
                return

            if parsed == "show_log":
                try:
                    self.app.push_screen(LogScreen())
                except Exception as e:
                    logger.warning("show_log 推屏失败: %s", e)
                return

            if parsed is None:
                try:
                    self.query_one("#message-bar", Static).update(
                        f"无效命令: {raw}  (输入 ? 查看帮助)"
                    )
                except Exception:
                    pass
                return

            self._on_action(parsed)

    # -----------------------------------------------------------------------
    # LogScreen（全屏日志界面，从 screens.py 移入以消除互指）
    # -----------------------------------------------------------------------

    from textual.screen import Screen as _Screen
    from textual.app import ComposeResult as _ComposeResult
    from textual.binding import Binding as _Binding

    class LogScreen(_Screen):
        """全屏日志界面。通过 L 键推入，Esc 返回。"""

        BINDINGS = [_Binding("escape", "app.pop_screen", "返回", show=True)]

        def compose(self) -> _ComposeResult:
            from textual.widgets import Footer, Header, RichLog
            yield Header()
            yield RichLog(id="log-screen-rich", max_lines=2000, wrap=True)
            yield Footer()

        def on_mount(self) -> None:
            try:
                log_area = self.app.query_one("#log-area", LogArea)
                src = log_area.query_one("#log-rich", RichLog)
                dst = self.query_one("#log-screen-rich", RichLog)
                lines = getattr(src, "_lines", None)
                if lines:
                    for line in lines:
                        dst.write(line)
                dst.scroll_end(animate=False)
                log_area._log_screen_ref = dst
            except Exception as e:
                logger.warning("LogScreen.on_mount 复制日志失败: %s", e)

        def on_unmount(self) -> None:
            try:
                log_area = self.app.query_one("#log-area", LogArea)
                log_area._log_screen_ref = None
            except Exception:
                pass

else:
    # Textual 不可用时提供占位类
    class PipelineSidebar:  # type: ignore[no-redef]
        pass

    class LLMStreamView:  # type: ignore[no-redef]
        pass

    class GenericStageView:  # type: ignore[no-redef]
        pass

    class L1aProgressView:  # type: ignore[no-redef]
        pass

    class MainArea:  # type: ignore[no-redef]
        pass

    class TwoBStageView:  # type: ignore[no-redef]
        pass

    class BlockProgressBar:  # type: ignore[no-redef]
        pass

    class BlockChatView:  # type: ignore[no-redef]
        pass

    class ResultView:  # type: ignore[no-redef]
        pass

    class LogArea:  # type: ignore[no-redef]
        pass

    class ReviewScreen:  # type: ignore[no-redef]
        pass

    class LogScreen:  # type: ignore[no-redef]
        pass
