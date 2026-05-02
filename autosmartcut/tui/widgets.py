"""tui/widgets.py — Textual Widget 组件。

包含：
- PipelineSidebar  侧边栏（节点状态）
- LLMStreamView    统一 LLM 流式视图（L2A/L2B/L2C 共用，支持多 slot + 插件）
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
    from autosmartcut.tui.logging.stream_hub import LogStreamHub
    from autosmartcut.tui.logging.repository import LogRepository
    from autosmartcut.tui.logging.context import RunLogContext
    from autosmartcut.tui.logging.screen_controller import LogScreenController
    from autosmartcut.tui.addons import DecisionsAddon, ResultAddon
    from autosmartcut.tui.stream_vm import SlotState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Spinner — 旋转动画状态（纯数据，无 Textual 依赖）
# ---------------------------------------------------------------------------

class Spinner:
    """旋转动画帧计数器。

    纯 Python，无 Textual 依赖，可独立测试。
    定时器管理（set_interval）由调用方 Widget 负责。

    用法::

        spinner = Spinner()
        spinner.tick()          # 推进一帧（在定时器回调里调用）
        frame = spinner.frame   # 获取当前帧字符
    """

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self) -> None:
        self._idx: int = 0

    def tick(self) -> None:
        """推进一帧。"""
        self._idx += 1

    def reset(self) -> None:
        """重置到第一帧。"""
        self._idx = 0

    @property
    def frame(self) -> str:
        """当前帧字符。"""
        return self.FRAMES[self._idx % len(self.FRAMES)]

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
    from textual import events

    from autosmartcut.cli.formatters import (
        format_decision_list,
        format_progress,
        format_review_summary,
        format_stats,
        parse_l1a_chunk_done,
        parse_l1a_intra_chunk_progress,
    )
    from autosmartcut.cli.progress_utils import format_duration as _format_duration

    _time_monotonic = time.monotonic

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
    # LLMStreamView — 统一 LLM 流式视图（L2A/L2B/L2C 共用）
    # -----------------------------------------------------------------------

    class LLMStreamView(Widget):
        """统一 LLM 流式视图：支持多 slot（并发/串行 LLM 调用）+ 插件面板。

        架构：
        - LLMStreamViewModel（tui/stream_vm.py）管理所有 slot 状态，纯 Python
        - 本 Widget 只做"读 ViewModel → 写 Textual 控件"的映射
        - 插件（DecisionsAddon / ResultAddon）挂载到 #stream-addon 容器

        生命周期：
        - begin_node(node_id, slots)：节点开始，重置 ViewModel，注册初始 slots
        - handle_progress(event)：L2 节点所有 progress 事件的统一入口
        - 50ms 节流 flush：只更新 active slot 的增量行
        - slot 切换（←/→）：全量重绘 active slot
        """

        can_focus = True

        BINDINGS = [
            Binding("left",  "prev_slot",        "上一个", show=False),
            Binding("right", "next_slot",        "下一个", show=False),
            Binding("t",     "toggle_reasoning", "思考",   show=False),
        ]

        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            from autosmartcut.tui.stream_vm import LLMStreamViewModel
            self._vm = LLMStreamViewModel()
            self._reasoning_expanded: bool = True
            self._flush_scheduled: bool = False
            self._addon: object = None  # StreamAddon | None
            self._spinner = Spinner()
            self._spinner_timer = None

        def compose(self):
            yield Static("", id="stream-status")
            yield Static("", id="stream-progress")
            yield RichLog(id="stream-reasoning", max_lines=500, wrap=True, auto_scroll=True)
            yield Static("", id="stream-reasoning-cur")
            yield Static("─" * 40, id="stream-divider")
            yield RichLog(id="stream-content", max_lines=200, wrap=True, auto_scroll=True)
            yield Static("", id="stream-content-cur")
            from textual.containers import Vertical as _V
            yield _V(id="stream-addon")

        def on_mount(self) -> None:
            try:
                self.query_one("#stream-addon").display = False
            except Exception:
                pass

        def on_unmount(self) -> None:
            self._stop_spinner()

        # ── 外部接口（MainArea 调用）──────────────────────────────────────

        def begin_node(self, node_id: str, slots: list[tuple[str, str]]) -> None:
            """节点开始：重置 ViewModel，注册初始 slots，清空 UI，卸载插件。"""
            from autosmartcut.tui.stream_vm import LLMStreamViewModel
            self._vm = LLMStreamViewModel()
            self._vm.register_slots(slots)
            self._reasoning_expanded = True
            self._clear_all_panels()
            self.unmount_addon()
            self._spinner.reset()
            self._start_spinner()
            try:
                self.focus()
            except Exception:
                pass

        def handle_progress(self, event: "PipelineEvent") -> None:
            """L2 节点所有 progress 事件的统一入口。

            MainArea 只做视图切换，L2 内部路由在此处理。
            """
            match event.phase:
                case "llm_stream":
                    self._handle_llm_stream(event.payload)
                case "2b_start":
                    self._on_2b_start(event.payload or {})
                case "2b_chunk":
                    self._on_2b_chunk(event.payload or {})
                case "2b_done":
                    self._on_2b_done(event.payload or {})
                case _:
                    # decision_start, review_start 等 → 更新状态栏
                    text = format_progress(event.node_id, event.phase, event.payload or {})
                    try:
                        self.query_one("#stream-status", Static).update(text)
                    except Exception:
                        pass

        def mount_addon(self, addon: object) -> None:
            """挂载插件 widget 到 #stream-addon 容器。"""
            self.unmount_addon()
            try:
                container = self.query_one("#stream-addon")
                container.mount(addon)
                container.display = True
                self._addon = addon
            except Exception as e:
                logger.warning("mount_addon 失败: %s", e)

        def unmount_addon(self) -> None:
            """卸载当前插件。"""
            try:
                container = self.query_one("#stream-addon")
                for child in list(container.children):
                    child.remove()
                container.display = False
            except Exception:
                pass
            self._addon = None

        # ── 内部：llm_stream 处理 ─────────────────────────────────────────

        def _handle_llm_stream(self, payload: dict) -> None:
            stage = payload.get("stage", "")
            evt = payload.get("event", "")
            match evt:
                case "reasoning_delta":
                    self._vm.feed_delta(stage, reasoning=payload.get("reasoning_delta", ""))
                    self._schedule_flush()
                case "content_delta":
                    self._vm.feed_delta(stage, content=payload.get("content_delta", ""))
                    self._schedule_flush()
                case "retry":
                    self._vm.mark_retry(
                        stage,
                        payload.get("attempt", 0),
                        payload.get("retry_reason", ""),
                    )
                    self._redraw_active_slot()
                case "result":
                    self._vm.mark_done(stage)
                    self._redraw_active_slot()

        # ── 内部：2b 专用事件 ─────────────────────────────────────────────

        def _on_2b_start(self, payload: dict) -> None:
            from autosmartcut.tui.addons import DecisionsAddon
            n = max(1, int(payload.get("n_blocks_r1_estimate", 1)))
            slots = [(f"block_{i}", f"块 {i + 1}") for i in range(n)]
            self._vm.register_slots(slots)
            self._update_progress_bar()
            self.mount_addon(DecisionsAddon())

        def _on_2b_chunk(self, payload: dict) -> None:
            bo = int(payload.get("block_ordinal", 0))
            slot_id = f"block_{bo}"
            # llm_stream 部分（thinking + content）
            self._handle_llm_stream(payload)
            # decisions 增量写入 addon_data
            decisions = payload.get("decisions")
            if decisions is not None:
                self._vm.set_addon_data(slot_id, "decisions", decisions)
                if slot_id == self._vm.get_active() and self._addon is not None:
                    new_items, _ = self._vm.flush_addon(slot_id, "decisions")
                    if new_items:
                        try:
                            self._addon.append_items(new_items)
                        except Exception:
                            pass

        def _on_2b_done(self, payload: dict) -> None:
            from autosmartcut.tui.addons import ResultAddon
            self.unmount_addon()
            result_addon = ResultAddon()
            self.mount_addon(result_addon)
            try:
                result_addon.show_result(
                    tokens=list(payload.get("tokens") or []),
                    keep_mask=list(payload.get("keep_mask") or []),
                    comprehension=dict(payload.get("comprehension") or {}),
                )
            except Exception as e:
                logger.warning("_on_2b_done show_result 失败: %s", e)

        # ── 50ms 节流 flush ───────────────────────────────────────────────

        def _schedule_flush(self) -> None:
            if not self._flush_scheduled:
                self._flush_scheduled = True
                self.set_timer(0.05, self._do_flush)

        def _do_flush(self) -> None:
            self._flush_scheduled = False
            active = self._vm.get_active()
            if not active:
                return
            new_r, r_cur, new_c, c_cur = self._vm.flush(active)
            self._write_incremental(new_r, r_cur, new_c, c_cur)
            self._update_status_bar()
            self._update_progress_bar()

        def _write_incremental(
            self,
            new_r: list[str],
            r_cur: str,
            new_c: list[str],
            c_cur: str,
        ) -> None:
            if new_r and self._reasoning_expanded:
                try:
                    rl = self.query_one("#stream-reasoning", RichLog)
                    for line in new_r:
                        rl.write(line)
                except Exception:
                    pass
            try:
                self.query_one("#stream-reasoning-cur", Static).update(
                    r_cur if self._reasoning_expanded else ""
                )
            except Exception:
                pass
            if new_c:
                try:
                    cl = self.query_one("#stream-content", RichLog)
                    for line in new_c:
                        cl.write(line)
                except Exception:
                    pass
            try:
                self.query_one("#stream-content-cur", Static).update(c_cur)
            except Exception:
                pass

        # ── 全量重绘（slot 切换 / retry / done）──────────────────────────

        def _redraw_active_slot(self) -> None:
            """active slot 变化后，全量重绘面板。"""
            active = self._vm.get_active()
            r_lines, r_cur, c_lines, c_cur = self._vm.flush_full(active)

            try:
                rl = self.query_one("#stream-reasoning", RichLog)
                rl.clear()
                if self._reasoning_expanded:
                    for line in r_lines:
                        rl.write(line)
            except Exception:
                pass
            try:
                self.query_one("#stream-reasoning-cur", Static).update(
                    r_cur if self._reasoning_expanded else ""
                )
            except Exception:
                pass
            try:
                cl = self.query_one("#stream-content", RichLog)
                cl.clear()
                for line in c_lines:
                    cl.write(line)
            except Exception:
                pass
            try:
                self.query_one("#stream-content-cur", Static).update(c_cur)
            except Exception:
                pass

            self._update_status_bar()
            self._update_progress_bar()

            # 插件全量刷新
            if self._addon is not None:
                slot = self._vm.get_slot(active)
                if slot is not None:
                    try:
                        self._addon.refresh_from_slot(slot)
                    except Exception:
                        pass
                    # 同步 addon flush 指针，避免后续增量重复
                    self._vm.flush_addon(active, "decisions")

        def _clear_all_panels(self) -> None:
            for wid, cls in [
                ("#stream-reasoning", RichLog),
                ("#stream-content", RichLog),
            ]:
                try:
                    self.query_one(wid, cls).clear()
                except Exception:
                    pass
            for wid in ["#stream-reasoning-cur", "#stream-content-cur",
                        "#stream-status", "#stream-progress"]:
                try:
                    self.query_one(wid, Static).update("")
                except Exception:
                    pass

        # ── 状态栏 / 进度条 ───────────────────────────────────────────────

        def _update_status_bar(self) -> None:
            active = self._vm.get_active()
            slot = self._vm.get_slot(active)
            if slot is None:
                return
            icons = {
                "idle": "○", "streaming": "🧠", "done": "✓",
                "failed": "✗", "retrying": "↻",
            }
            icon = icons.get(slot.status, "?")
            if slot.status == "retrying":
                detail = f"重试（第 {slot.attempt} 次）"
            elif slot.status == "done":
                detail = "完成"
            elif slot.reasoning_done:
                detail = "生成中…"
            else:
                detail = "思考中…"
            try:
                self.query_one("#stream-status", Static).update(
                    f"{icon} [{slot.label}] {detail}"
                )
            except Exception:
                pass

        def _update_progress_bar(self) -> None:
            progress = self._vm.get_progress()
            if len(progress) <= 1:
                try:
                    self.query_one("#stream-progress", Static).update("")
                except Exception:
                    pass
                return
            active = self._vm.get_active()
            frame = self._spinner.frame
            icons = {
                "idle": frame, "streaming": frame, "done": "✓",
                "failed": "✗", "retrying": frame,
            }
            colors = {
                "idle": "dim", "streaming": "dark_orange", "done": "green",
                "failed": "red", "retrying": "yellow",
            }
            parts = []
            for sid, label, status in progress:
                icon = icons.get(status, "?")
                color = colors.get(status, "")
                if sid == active:
                    parts.append(f"[bold reverse]{icon} {label}[/bold reverse]")
                elif color:
                    parts.append(f"[{color}]{icon} {label}[/{color}]")
                else:
                    parts.append(f"{icon} {label}")
            hint = "  [dim]←/→ 切换  t 思考[/dim]"
            try:
                self.query_one("#stream-progress", Static).update(
                    "  ".join(parts) + hint
                )
            except Exception:
                pass

        # ── 键盘动作 ──────────────────────────────────────────────────────

        def action_prev_slot(self) -> None:
            self._vm.prev_slot()
            self._redraw_active_slot()

        def action_next_slot(self) -> None:
            self._vm.next_slot()
            self._redraw_active_slot()

        def action_toggle_reasoning(self) -> None:
            self._reasoning_expanded = not self._reasoning_expanded
            self._redraw_active_slot()

        def reset(self) -> None:
            """兼容旧调用（MainArea._switch_to_stream_view 内部用 begin_node 替代）。"""
            from autosmartcut.tui.stream_vm import LLMStreamViewModel
            self._vm = LLMStreamViewModel()
            self._clear_all_panels()
            self.unmount_addon()
            self._stop_spinner()

        # ── 旋转动画 ──────────────────────────────────────────────────────

        def _start_spinner(self) -> None:
            if self._spinner_timer is None:
                self._spinner_timer = self.set_interval(0.1, self._tick_spinner)

        def _stop_spinner(self) -> None:
            if self._spinner_timer is not None:
                try:
                    self._spinner_timer.stop()
                except Exception:
                    pass
                self._spinner_timer = None

        def _tick_spinner(self) -> None:
            self._spinner.tick()
            # 若所有块都已完成，停止定时器
            progress = self._vm.get_progress()
            has_active = any(
                s in ("idle", "streaming", "retrying")
                for _, _, s in progress
            )
            if not has_active:
                self._stop_spinner()
                return
            self._update_progress_bar()

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
    # L3ProgressView
    # -----------------------------------------------------------------------

    class _L3ViewState:
        """L3 进度视图的声明式状态（纯数据，无 Textual 依赖）。"""

        __slots__ = (
            "header", "vad_text", "vad_done",
            "render_text", "render_done", "bar_text",
        )

        def __init__(self) -> None:
            self.header: str = ""
            self.vad_text: str = ""
            self.vad_done: bool = False
            self.render_text: str = ""
            self.render_done: bool = False
            self.bar_text: str = ""

    class L3ProgressView(Widget):
        """L3 专用进度视图（ViewModel + 声明式渲染）。

        布局：
        - #l3-header  灰框上方：描述行
        - #l3-log     灰框（RichLog）：所有内容

        事件处理只更新 _L3ViewState，旋转动画只递增计数器，
        然后统一调 _redraw() 根据 ViewState 重建 RichLog 内容。
        """

        def compose(self):
            yield Static("", id="l3-header")
            yield RichLog(id="l3-log", max_lines=200, wrap=True, auto_scroll=True)

        def on_mount(self) -> None:
            self._state = _L3ViewState()
            self._render_total: int = 0
            self._render_start_time: float = 0.0
            self._spinner = Spinner()
            self._spinner_timer = None

        def on_unmount(self) -> None:
            self._stop_spinner()

        # ── 事件处理（只更新 ViewState）────────────────────────────────────

        def handle_progress(self, event: "PipelineEvent") -> None:
            phase = event.phase
            p = event.payload or {}

            if phase == "resolve_start":
                self._state.header = f"  L3 执行 · {p.get('segment_count', 0)} 个保留段"

            elif phase == "vad_snap_start":
                self._state.vad_text = f"静音吸附中（snap_radius={p.get('snap_radius', 0):.3f}s）..."
                self._state.vad_done = False
                self._start_spinner()

            elif phase == "vad_snap_done":
                self._stop_spinner()
                count = p.get("silence_count", 0)
                snap_r = p.get("snap_radius", 0)
                elapsed = p.get("elapsed_sec", 0)
                self._state.vad_text = (
                    f"静音吸附完成：{count} 条静音区间，"
                    f"snap_radius={snap_r:.3f}s（{elapsed:.1f}s）"
                )
                self._state.vad_done = True

            elif phase == "render_start":
                self._render_total = int(p.get("segment_count", 0))
                self._render_start_time = _time_monotonic()
                self._state.render_text = f"渲染中（共 {self._render_total} 个 cut 单元）..."
                self._state.render_done = False
                self._state.bar_text = self._make_bar(0, self._render_total, remain_str="")
                self._start_spinner()

            elif phase == "render_progress":
                done = int(p.get("done", 0))
                total = int(p.get("total", self._render_total or 1))
                self._render_total = total
                self._state.bar_text = self._make_bar(
                    done, total, remain_str=self._estimate_remain(done, total),
                )

            elif phase == "render_done":
                self._stop_spinner()
                elapsed = p.get("elapsed_sec", 0)
                self._state.render_text = f"渲染完成（{elapsed:.1f}s）"
                self._state.render_done = True
                self._state.bar_text = self._make_bar(
                    self._render_total, self._render_total, remain_str="",
                )

            self._redraw()

        # ── 声明式渲染 ────────────────────────────────────────────────────

        def _redraw(self) -> None:
            """根据 _state 重建 #l3-header 和 #l3-log 的全部内容。"""
            from rich.text import Text as _Text

            s = self._state

            # header
            try:
                self.query_one("#l3-header", Static).update(s.header)
            except Exception:
                pass

            # 构建行列表：(text, is_yellow)
            lines: list[tuple[str, bool]] = []
            lines.append(("", False))  # 顶部空行

            if s.vad_text:
                prefix = "✓" if s.vad_done else self._spinner.frame
                lines.append((f"  {prefix} {s.vad_text}", False))

            if s.render_text:
                lines.append(("", False))  # 空行
                lines.append(("", False))  # 空行
                prefix = "✓" if s.render_done else self._spinner.frame
                lines.append((f"  {prefix} {s.render_text}", False))

            if s.bar_text:
                lines.append(("", False))  # 空行
                lines.append((s.bar_text, True))  # 黄色

            # 写入 RichLog
            try:
                log = self.query_one("#l3-log", RichLog)
                log.clear()
                for text, yellow in lines:
                    if yellow and text:
                        log.write(_Text(text, style="yellow"))
                    else:
                        log.write(text)
            except Exception:
                pass

        # ── 旋转动画 ──────────────────────────────────────────────────────

        def _start_spinner(self) -> None:
            self._stop_spinner()
            self._spinner.reset()
            self._spinner_timer = self.set_interval(0.1, self._tick_spinner)

        def _stop_spinner(self) -> None:
            if self._spinner_timer is not None:
                try:
                    self._spinner_timer.stop()
                except Exception:
                    pass
                self._spinner_timer = None

        def _tick_spinner(self) -> None:
            self._spinner.tick()
            self._redraw()

        # ── 剩余时间估算 ──────────────────────────────────────────────────

        def _estimate_remain(self, done: int, total: int) -> str:
            if done <= 0 or total <= 0 or self._render_start_time <= 0:
                return ""
            elapsed = _time_monotonic() - self._render_start_time
            if elapsed <= 0:
                return ""
            remain_sec = elapsed / done * (total - done)
            return _format_duration(remain_sec)

        def _make_bar(self, done: int, total: int, *, remain_str: str) -> str:
            if total <= 0:
                return ""
            pct = done / total * 100
            bar_width = 24
            filled = int(pct / 100 * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            if remain_str:
                remain = f"  剩余 {remain_str}"
            elif done > 0:
                remain = ""
            else:
                remain = "  正在计算剩余时间..."
            return f"  {bar} {done}/{total}  ({pct:.0f}%){remain}"

    # -----------------------------------------------------------------------
    # MainArea
    # -----------------------------------------------------------------------

    class MainArea(Widget):
        """主区域：根据活跃节点切换视图。

        视图路由：
        - L1  → L1aProgressView（动态 mount/remove）
        - L2（非 L2D）→ LLMStreamView（统一流式视图）
        - L2D → ReviewScreen（need_input 事件触发）
        - L3 / 其他 → GenericStageView
        """

        def compose(self):
            yield GenericStageView(id="generic-view")
            yield LLMStreamView(id="stream-view")

        def show_stage_progress(self, node_id: str) -> None:
            if node_id == "l1_perception":
                self._switch_to_l1a_view()
            elif node_id.startswith("l2") and node_id != "l2d_human":
                self._switch_to_stream_view(node_id)
            elif node_id == "l3_execute":
                self._switch_to_l3_view()
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
            elif node_id.startswith("l2") and node_id != "l2d_human":
                # L2 节点完成：隐藏流式视图，切回 generic 并记录摘要
                try:
                    self.query_one("#stream-view", LLMStreamView).display = False
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
            elif node_id == "l3_execute":
                self._teardown_l3_view(summary_line)
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
            elif event.node_id.startswith("l2") and event.node_id != "l2d_human":
                # 所有 L2（非 L2D）progress 事件统一转发给 LLMStreamView
                try:
                    self.query_one("#stream-view", LLMStreamView).handle_progress(event)
                except Exception:
                    pass
            elif event.node_id == "l3_execute":
                try:
                    self.query_one("#l3-view", L3ProgressView).handle_progress(event)
                except Exception:
                    pass
            else:
                # 其他 → GenericStageView 状态栏
                try:
                    gv = self.query_one("#generic-view", GenericStageView)
                    gv.set_current(format_progress(event.node_id, event.phase, event.payload))
                except Exception:
                    pass

        def _switch_to_l1a_view(self) -> None:
            try:
                self.query_one("#generic-view", GenericStageView).display = False
            except Exception:
                pass
            try:
                self.query_one("#stream-view", LLMStreamView).display = False
            except Exception:
                pass
            existing = self.query("#l1a-view")
            if not existing:
                try:
                    self.mount(L1aProgressView(id="l1a-view"))
                except Exception:
                    pass

        def _switch_to_stream_view(self, node_id: str) -> None:
            """切换到 LLMStreamView，根据节点注册初始 slots。"""
            try:
                self.query_one("#generic-view", GenericStageView).display = False
            except Exception:
                pass
            try:
                sv = self.query_one("#stream-view", LLMStreamView)
                sv.display = True
                match node_id:
                    case "l2a_comprehension":
                        sv.begin_node(node_id, [("r1", "R1 粗理解"), ("r2", "R2 精化")])
                    case "l2b_decision":
                        # slots 由 2b_start event 动态注册
                        sv.begin_node(node_id, [])
                    case "l2c_review":
                        sv.begin_node(node_id, [("review_1", "审核轮 1")])
                    case _:
                        sv.begin_node(node_id, [(node_id, node_id)])
            except Exception:
                pass

        def _teardown_l1a_view(self, summary_line: str) -> None:
            try:
                self.query_one("#l1a-view", L1aProgressView).remove()
            except Exception:
                pass
            self._ensure_generic_view()
            try:
                self.query_one("#generic-view", GenericStageView).append_frozen(summary_line)
            except Exception:
                pass

        def _switch_to_l3_view(self) -> None:
            try:
                self.query_one("#generic-view", GenericStageView).display = False
            except Exception:
                pass
            try:
                self.query_one("#stream-view", LLMStreamView).display = False
            except Exception:
                pass
            existing = self.query("#l3-view")
            if not existing:
                try:
                    self.mount(L3ProgressView(id="l3-view"))
                except Exception:
                    pass

        def _teardown_l3_view(self, summary_line: str) -> None:
            try:
                self.query_one("#l3-view", L3ProgressView).remove()
            except Exception:
                pass
            self._ensure_generic_view()
            try:
                self.query_one("#generic-view", GenericStageView).append_frozen(summary_line)
            except Exception:
                pass

        def _ensure_generic_view(self) -> None:
            try:
                self.query_one("#generic-view", GenericStageView).display = True
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
        """日志区域：订阅 LogStreamHub，50ms batch 节流写入 RichLog。"""

        def __init__(self, hub: "LogStreamHub | None" = None, **kwargs) -> None:
            super().__init__(**kwargs)
            self._hub = hub
            self._hub_token: int | None = None
            self._pending: list[str] = []
            self._flush_scheduled: bool = False

        def compose(self):
            yield RichLog(id="log-rich", max_lines=2000, wrap=True)

        def on_mount(self) -> None:
            if self._hub is not None:
                self._hub_token = self._hub.subscribe(self._on_hub_line)

        def on_unmount(self) -> None:
            if self._hub is not None and self._hub_token is not None:
                self._hub.unsubscribe(self._hub_token)
                self._hub_token = None

        def _on_hub_line(self, line: str) -> None:
            """Hub 回调：积攒行，50ms 后批量写入（节流）。"""
            self._pending.append(line)
            if not self._flush_scheduled:
                self._flush_scheduled = True
                self.set_timer(0.05, self._flush_pending)

        def _flush_pending(self) -> None:
            self._flush_scheduled = False
            if not self._pending:
                return
            lines, self._pending = self._pending, []
            try:
                rl = self.query_one("#log-rich", RichLog)
                for line in lines:
                    rl.write(line)
            except Exception:
                pass

        def append_log(self, level: str, node_id: str, message: str) -> None:
            """供外部直接调用（如 error 路径）。走 Hub 或直接写。"""
            prefix = f"[{level}] " if level not in ("INFO", "") else ""
            node_prefix = f"[{node_id}] " if node_id else ""
            text = f"{prefix}{node_prefix}{message}"
            if self._hub is not None:
                self._hub.publish(text)
                return
            try:
                self.query_one("#log-rich", RichLog).write(text)
            except Exception:
                pass

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
                    mk = getattr(self.app, "make_log_screen", None)
                    if callable(mk):
                        self.app.push_screen(mk())
                    else:
                        logger.warning("当前 App 不支持 make_log_screen，无法打开日志屏")
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
        """全屏日志界面：历史 run_*.log + 实时 Hub；L 进入，Esc 返回，F 切换跟随，End 回底。"""

        BINDINGS = [
            _Binding("escape", "app.pop_screen", "返回", show=True),
            _Binding("f", "toggle_follow", "跟随", show=True),
            _Binding("end", "jump_bottom", "底部", show=True),
        ]

        CSS = """
        LogScreen Vertical {
            height: 1fr;
        }
        #log-screen-status {
            height: 1;
            color: $text-muted;
        }
        #log-screen-rich {
            height: 1fr;
        }
        """

        def __init__(
            self,
            hub: "LogStreamHub | None" = None,
            repository: "LogRepository | None" = None,
            context: "RunLogContext | None" = None,
            **kwargs,
        ) -> None:
            super().__init__(**kwargs)
            self._hub = hub
            self._repository = repository or LogRepository()
            self._context = context
            self._controller: "LogScreenController | None" = None

        def compose(self) -> _ComposeResult:
            from textual.widgets import Footer, Header, RichLog
            from textual.containers import Vertical
            yield Header()
            with Vertical():
                yield Static("", id="log-screen-status")
                yield RichLog(id="log-screen-rich", max_lines=2000, wrap=True)
            yield Footer()

        async def on_mount(self) -> None:
            import asyncio as _asyncio
            from autosmartcut.tui.logging.screen_controller import LogScreenController
            from textual.widgets import RichLog as _RichLog

            if self._hub is None or self._context is None:
                try:
                    self.query_one("#log-screen-status", Static).update(
                        "无法加载日志（缺少 LogStreamHub 或 RunLogContext）"
                    )
                except Exception:
                    pass
                return

            try:
                rl = self.query_one("#log-screen-rich", _RichLog)
                st = self.query_one("#log-screen-status", Static)
            except Exception as e:
                logger.warning("LogScreen.on_mount 查询控件失败: %s", e)
                return

            self._controller = LogScreenController(
                self._hub,
                self._repository,
                self._context,
                schedule_flush=lambda: self.set_timer(0.05, self._flush_live),
            )
            self._controller.attach(
                rich_log=rl,
                status_static=st,
                is_suppressed=lambda: False,
            )

            # 1. 显示加载状态
            try:
                st.update("加载历史日志…")
            except Exception:
                pass

            # 2. 异步读文件（不阻塞主线程）
            try:
                lines, n_files, n_lines = await _asyncio.to_thread(
                    self._controller.load_history_lines_sync
                )
            except Exception as e:
                logger.warning("LogScreen 历史加载失败: %s", e)
                lines, n_files, n_lines = [], 0, 0

            # 3. 分批写入 RichLog，每批 yield 一次让 Textual 渲染
            _BATCH = 500
            self._controller.begin_batch_write()
            try:
                for i in range(0, len(lines), _BATCH):
                    for line in lines[i : i + _BATCH]:
                        rl.write(line)
                    await _asyncio.sleep(0)
                if lines:
                    rl.scroll_end(animate=False)
            finally:
                self._controller.end_batch_write()

            # 4. 更新元数据
            self._controller.set_history_meta(n_files, n_lines)

            # 5. 历史写完后再订阅实时流（保证顺序）
            self._controller.subscribe_live()

            # 6. 滚动监听
            try:
                self.watch(rl, "scroll_y", self._watch_scroll_y)
            except Exception:
                pass

        def on_unmount(self) -> None:
            if self._controller is not None:
                self._controller.detach()
                self._controller = None

        def _flush_live(self) -> None:
            """timer 回调，触发 controller 的 live batch flush。"""
            if self._controller is not None:
                self._controller.flush_live_pending()

        def _watch_scroll_y(self, old: object, new: object) -> None:
            if self._controller is not None:
                self._controller.notify_scroll_y_changed(old, new)

        def on_mouse_scroll_up(self, _event: "events.MouseScrollUp") -> None:
            if self._controller is not None:
                self._controller.notify_user_scroll_up()

        def action_toggle_follow(self) -> None:
            if self._controller is not None:
                self._controller.toggle_follow()

        def action_jump_bottom(self) -> None:
            if self._controller is not None:
                self._controller.set_follow(True)

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

    class L3ProgressView:  # type: ignore[no-redef]
        pass

    class MainArea:  # type: ignore[no-redef]
        pass

    class LogArea:  # type: ignore[no-redef]
        pass

    class ReviewScreen:  # type: ignore[no-redef]
        pass

    class LogScreen:  # type: ignore[no-redef]
        pass
