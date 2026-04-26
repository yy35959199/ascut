"""tui_adapter.py — TUI 适配器：基于 Textual 框架。

将 EventBus 事件映射到 Textual 组件更新，提供三区域布局：
  侧边栏（流水线进度）| 主区域（当前阶段 / ReviewScreen）| 日志区域

格式化函数（_format_decision_list、_format_review_summary、_format_stats）
从 intelligence_2d_shell.py 迁移至此，保持渲染逻辑集中在消费层。
"""
from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from autosmartcut.intelligence_2d_core import DisplayData
    from autosmartcut.pipeline_events import PipelineEvent
    from autosmartcut.pipeline_session import PipelineSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 视图模型（View Model）：payload 解析与渲染之间的稳定接口
# ---------------------------------------------------------------------------

@dataclass
class L1aChunkProgressState:
    """L1A 块内进度的视图模型（对应 asr_intra_chunk_progress 事件）。

    消费层（TUI widget、CLI 格式化函数）只使用此结构的字段，
    不直接访问 ProgressEvent.payload，便于日后替换渲染框架。
    """
    chunk_id: int        # 当前块序号（0-based）
    total_chunks: int    # 总块数
    pct: float           # 整体进度百分比 0.0–100.0
    remain_str: str      # 已格式化的剩余时间，如 "2m30s" 或 "—"
    speed_str: str       # 已格式化的速度，如 "3.2x"，无数据时为空字符串


@dataclass
class L1aChunkDoneState:
    """L1A 块完成的视图模型（对应 asr_chunk_done 事件）。"""
    chunk_id: int
    total_chunks: int
    text: str            # 完整识别文本（已 strip）


def _parse_l1a_intra_chunk_progress(payload: dict) -> L1aChunkProgressState:
    """从 asr_intra_chunk_progress payload 提取视图模型。

    所有 payload.get() 调用集中在此，消费层不再直接访问 payload。
    """
    from autosmartcut.progress_utils import format_duration
    remaining = payload.get("remaining_sec")
    speed = payload.get("estimated_speed")
    return L1aChunkProgressState(
        chunk_id=int(payload.get("chunk_id", 0)),
        total_chunks=int(payload.get("total_chunks", 1)),
        pct=float(payload.get("pct", 0.0)),
        remain_str=format_duration(remaining) if remaining is not None else "—",
        speed_str=f"{speed:.2f}x" if speed else "",
    )


def _parse_l1a_chunk_done(payload: dict) -> L1aChunkDoneState:
    """从 asr_chunk_done payload 提取视图模型。"""
    return L1aChunkDoneState(
        chunk_id=int(payload.get("chunk_id", 0)),
        total_chunks=int(payload.get("total_chunks", 1)),
        text=str(payload.get("text_full", payload.get("text_preview", ""))).strip(),
    )


# ---------------------------------------------------------------------------
# 格式化函数（从 intelligence_2d_shell.py 迁移，渲染逻辑属于消费层）
# ---------------------------------------------------------------------------

def _format_review_summary(review_report: dict) -> str:
    """格式化 2c 审核报告为文本。"""
    if not review_report:
        return "（无审核报告）"

    lines: list[str] = []
    verdict = review_report.get("verdict", "unknown")
    lines.append(f"审核结论: {verdict}")

    checklist = review_report.get("checklist", [])
    judgments = review_report.get("judgments", [])

    judgment_map: dict[int, dict] = {}
    for j in judgments:
        idx = j.get("checklist_index", j.get("index", -1))
        judgment_map[idx] = j

    for i, item in enumerate(checklist):
        label = item.get("label", item.get("text", f"项目 {i}"))
        priority = item.get("priority", "")
        j = judgment_map.get(i, {})
        covered = j.get("covered", j.get("pass", False))
        mark = "✓" if covered else "✗"
        priority_tag = f"[{priority}]" if priority else ""
        lines.append(f"  {mark} {priority_tag} {label}")

    return "\n".join(lines)


def _format_decision_list(display_data: "DisplayData") -> str:
    """格式化决策列表为文本，按 outline_blocks 分组。"""
    tokens = display_data.tokens
    mask = display_data.effective_mask
    comp = display_data.comprehension
    outline_blocks = comp.get("outline_blocks", [])

    lines: list[str] = []

    if outline_blocks:
        for block in outline_blocks:
            block_label = block.get("label", block.get("title", ""))
            start = block.get("start", 0)
            end = block.get("end", len(tokens))
            lines.append(f"\n── {block_label} [{start}-{end}] ──")
            for i in range(start, min(end + 1, len(tokens))):
                keep = mask[i]["keep"] if i < len(mask) else True
                status = "[保留]" if keep else "[删除]"
                text = tokens[i].get("text", "")
                preview = (text[:60] + "…") if len(text) > 63 else text
                if not preview:
                    preview = "(空)"
                lines.append(f"  {status}[{i}] {preview}")
    else:
        for i, tok in enumerate(tokens):
            keep = mask[i]["keep"] if i < len(mask) else True
            status = "[保留]" if keep else "[删除]"
            text = tok.get("text", "")
            preview = (text[:60] + "…") if len(text) > 63 else text
            if not preview:
                preview = "(空)"
            lines.append(f"  {status}[{i}] {preview}")

    return "\n".join(lines)


def _format_stats(stats: dict) -> str:
    """格式化统计栏。"""
    return (
        f"保留: {stats.get('keep_count', 0)} | "
        f"删除: {stats.get('cut_count', 0)} | "
        f"总数: {stats.get('total', 0)} | "
        f"人工修改: {stats.get('override_count', 0)}"
    )


def _format_progress(node_id: str, phase: str, payload: dict) -> str:
    """将结构化 ProgressEvent 格式化为人类可读文本。

    消费层（CLI/TUI）共用此函数，保证输出一致。
    未知 node_id/phase 组合降级为 "[node_id] phase"。
    """
    p = payload
    match (node_id, phase):
        # ── L1A ──────────────────────────────────────────────────────────
        case ("l1a_asr", "transcode_start"):
            return "  音频转码中..."
        case ("l1a_asr", "transcode_done"):
            return f"  音频转码完成 ({p.get('elapsed_sec', 0):.1f}s)"
        case ("l1a_asr", "vad_start"):
            return "  语音段分析中..."
        case ("l1a_asr", "vad_done"):
            return (
                f"  语音段分析完成 ({p.get('elapsed_sec', 0):.1f}s)，"
                f"检测到 {p.get('speech_segment_count', 0)} 个语音段"
            )
        case ("l1a_asr", "plan_done"):
            dur = p.get("total_audio_sec", 0)
            n = p.get("total_chunks", 0)
            return f"  音频 {dur:.0f}s 分为 {n} 块，开始识别..."
        case ("l1a_asr", "asr_chunk_start"):
            chunk_id = p.get("chunk_id", 0)
            total = p.get("total_chunks", 1)
            return f"  正在计算剩余时间..." if chunk_id == 0 else f"  ASR [{chunk_id + 1}/{total}] 推理中..."
        case ("l1a_asr", "asr_chunk_done"):
            chunk_id = p.get("chunk_id", 0)
            total = p.get("total_chunks", 1)
            speed = p.get("estimated_speed")
            speed_str = f"{speed:.2f}x" if speed else "—"
            preview = p.get("text_preview", "")
            return f"  ASR [{chunk_id + 1}/{total}] 完成 speed={speed_str} | {preview[:50]}"
        case ("l1a_asr", "asr_computing_speed"):
            return "  正在计算剩余时间..."
        case ("l1a_asr", "asr_intra_chunk_progress"):
            s = _parse_l1a_intra_chunk_progress(p)
            bar_width = 20
            filled = int(s.pct / 100 * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            speed = f" {s.speed_str}" if s.speed_str else ""
            return f"  [{s.chunk_id+1}/{s.total_chunks}] {bar} {s.pct:.0f}%  剩余 {s.remain_str}{speed}"
        case ("l1a_asr", "postprocess_done"):
            return (
                f"  ASR 完成：{p.get('sentence_count', 0)} 句，"
                f"{p.get('raw_text_length', 0)} 字符"
            )
        # ── L1B ──────────────────────────────────────────────────────────
        case ("l1b_align", "aligner_loading"):
            return "  对齐模型加载中..."
        case ("l1b_align", "aligner_loaded"):
            return f"  对齐模型加载完成 ({p.get('elapsed_sec', 0):.1f}s)"
        case ("l1b_align", "align_start"):
            return f"  强制对齐中（{p.get('sentence_count', 0)} 句）..."
        case ("l1b_align", "align_done"):
            return (
                f"  对齐完成 ({p.get('elapsed_sec', 0):.1f}s)："
                f"{p.get('aligned_count', 0)}/{p.get('sentence_count', 0)} 句已对齐"
            )
        # ── L2A ──────────────────────────────────────────────────────────
        case ("l2a_comprehension", "r1_start"):
            return "  R1 粗理解中..."
        case ("l2a_comprehension", "r1_done"):
            return f"  R1 完成 ({p.get('elapsed_sec', 0):.1f}s)"
        case ("l2a_comprehension", "r2_start"):
            return "  R2 精化主旨与分块中..."
        case ("l2a_comprehension", "r2_done"):
            purpose = p.get("purpose", "")
            preview = (purpose[:60] + "…") if len(purpose) > 63 else purpose
            return (
                f"  L2A 完成 ({p.get('elapsed_sec', 0):.1f}s)："
                f"{p.get('block_count', 0)} 块，{p.get('correction_count', 0)} 处纠错 | {preview}"
            )
        # ── L2B ──────────────────────────────────────────────────────────
        case ("l2b_decision", "decision_start"):
            mode = p.get("mode", "")
            round_ = p.get("review_round", 0)
            rerun = "（2c 修正重跑）" if p.get("is_rerun") else ""
            return f"  决策中（轮次 {round_}，模式 {mode}）{rerun}..."
        case ("l2b_decision", "block_done"):
            return f"  决策块 {p.get('block_index', 0) + 1}/{p.get('total_blocks', 1)}"
        case ("l2b_decision", "decision_done"):
            return (
                f"  L2B 完成 ({p.get('elapsed_sec', 0):.1f}s)："
                f"保留 {p.get('keep_count', 0)}，删除 {p.get('cut_count', 0)}，"
                f"共 {p.get('total', 0)} 句"
            )
        # ── L2C ──────────────────────────────────────────────────────────
        case ("l2c_review", "review_start"):
            return f"  审核中（轮次 {p.get('review_round', 0)}）..."
        case ("l2c_review", "review_done"):
            verdict = p.get("verdict", "")
            rate = p.get("must_pass_rate", "")
            fix = p.get("fix_count", 0)
            return (
                f"  L2C 完成 ({p.get('elapsed_sec', 0):.1f}s)："
                f"verdict={verdict} must通过率={rate} 修正={fix}条"
            )
        # ── L2D ──────────────────────────────────────────────────────────
        case ("l2d_human", "waiting_input"):
            return "  等待人工审阅..."
        case ("l2d_human", "action_processed"):
            msg = p.get("result_message", "")
            return f"  操作已处理{': ' + msg if msg else ''}"
        # ── L3 Precompute ─────────────────────────────────────────────────
        case ("l3_precompute", "precompute_start"):
            return f"  L3 预计算中（{p.get('sentence_count', 0)} 句）..."
        case ("l3_precompute", "precompute_done"):
            return (
                f"  L3 预计算完成 ({p.get('elapsed_sec', 0):.1f}s)："
                f"{p.get('clip_count', 0)} 个分片"
            )
        # ── L3 Execute ────────────────────────────────────────────────────
        case ("l3_execute", "resolve_start"):
            return f"  L3 编排中（{p.get('segment_count', 0)} 个保留段）..."
        case ("l3_execute", "encode_start"):
            return f"  编码中（{p.get('segment_count', 0)} 段）..."
        case ("l3_execute", "encode_progress"):
            idx = p.get("segment_index", 0)
            total = p.get("total_segments", 1)
            return f"  编码进度 {idx + 1}/{total}"
        case ("l3_execute", "assemble_start"):
            return "  合并片段中..."
        case ("l3_execute", "execute_done"):
            out = p.get("output_path", "")
            return f"  L3 完成 ({p.get('elapsed_sec', 0):.1f}s) → {out}"
        # ── 未知 ──────────────────────────────────────────────────────────
        case _:
            return f"  [{node_id}] {phase}"


try:
    from textual.app import App, ComposeResult
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, VerticalScroll
    from textual.screen import Screen
    from textual.widget import Widget
    from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static

    _TEXTUAL_AVAILABLE = True
except ImportError:
    _TEXTUAL_AVAILABLE = False
    logger.warning("Textual 未安装，TUIAdapter 不可用（pip install textual）")


# ---------------------------------------------------------------------------
# TUIAdapter
# ---------------------------------------------------------------------------

class TUIAdapter:
    """TUI 适配器：基于 Textual 框架，将 EventBus 事件映射到 Textual 组件更新。"""

    def __init__(self, session: "PipelineSession") -> None:
        self._session = session
        self._app: "PipelineApp | None" = None
        session.subscribe(self._handle_event)

    def _handle_event(self, event: "PipelineEvent") -> None:
        """将事件转发给 Textual App。

        ⚠️  注意：不能用 call_from_thread()。
        session.start_async() 与 app.run_async() 同跑在一个 asyncio event loop
        里（同一线程）。Textual 的 call_from_thread() 内部有明确的线程检查：
            if self._thread_id == threading.get_ident():
                raise RuntimeError("must run in a different thread from the app")
        在同线程调用必然抛 RuntimeError，若被 except 静默吞掉则所有事件全部丢失，
        TUI 将永远停在"等待流水线启动..."无法推进。

        正确做法：call_later()——通过 Textual 消息泵调度回调，同线程/同 event loop
        内完全安全，不做线程检查。
        """
        if self._app is not None:
            try:
                self._app.call_later(self._app.handle_pipeline_event, event)
            except Exception as e:
                logger.warning("TUIAdapter._handle_event 转发失败: %s", e)

    async def start_async(self) -> None:
        """异步启动 TUI。同时运行 Textual App 和 PipelineSession。"""
        if not _TEXTUAL_AVAILABLE:
            raise RuntimeError("Textual 未安装，无法启动 TUI 模式（pip install textual）")
        self._app = PipelineApp(self._session)
        await asyncio.gather(
            self._app.run_async(),
            self._session.start_async(),
        )


# ---------------------------------------------------------------------------
# PipelineApp
# ---------------------------------------------------------------------------

if _TEXTUAL_AVAILABLE:

    class PipelineApp(App):
        """Textual App 主体：三区域布局。"""

        TITLE = "AutoSmartCut TUI"
        CSS = """
        PipelineSidebar {
            width: 24;
            border-right: solid $primary;
            padding: 1;
        }
        MainArea {
            width: 1fr;
            height: 1fr;
            padding: 1;
        }
        LogArea {
            height: 8;
            border-top: solid $primary;
            padding: 0 1;
        }
        .node-pending  { color: $text-muted; }
        .node-running  { color: $warning; }
        .node-completed { color: $success; }
        .node-failed   { color: $error; }
        .node-skipped  { color: $text-muted; }
        GenericStageView {
            height: 1fr;
            layout: vertical;
        }
        #generic-current {
            height: 1;
            color: $text-muted;
        }
        L1aProgressView {
            height: 1fr;
            layout: vertical;
        }
        #l1a-progress-bar {
            height: 2;
            color: $warning;
        }
        #l1a-text-log {
            height: 1fr;
            border: none;
        }
        #l1a-chunk-status {
            height: 1;
            color: $text-muted;
        }
        """

        BINDINGS = [
            Binding("p", "pause", "暂停"),
            Binding("l", "show_log", "日志"),
            Binding("q", "quit_app", "退出"),
        ]

        def __init__(self, session: "PipelineSession") -> None:
            super().__init__()
            self._session = session
            self._loguru_sink_id: int | None = None
            self._force_exit: bool = False  # Q键或强制中止时设为True，on_unmount触发os._exit
            self._graceful_quit: bool = False  # 等待当前阶段完成后退出
            self._original_stderr = None

        def on_mount(self) -> None:
            """注册 loguru TUI sink，将所有日志转发到 LogArea。
            同时重定向 sys.stderr，防止第三方库（transformers 等）直接写 stderr
            覆盖 Textual 的 alternate screen buffer。

            必须在 on_mount 而非 __init__ 中注册：此时 Textual 消息泵已就绪，
            call_later() 可以安全调用。在 __init__ 中注册会导致 sink 回调在
            消息泵启动前触发，call_later() 无法投递。
            """
            import io
            # 重定向 sys.stderr：把第三方库直接写 stderr 的输出丢弃，
            # 防止覆盖 Textual 的 alternate screen buffer。
            # 原始 stderr 已在 setup_logging_tui 中被 loguru 接管（suppress_stderr=True），
            # 此处只是额外防御直接调用 sys.stderr.write() 的代码路径。
            self._original_stderr = sys.stderr
            sys.stderr = io.StringIO()

            from loguru import logger as loguru_logger

            def _tui_sink(message: object) -> None:
                # message 是 loguru 的 Message 对象，str() 得到格式化后的文本
                text = str(message).rstrip("\n")
                self.call_later(self._append_to_log_area, text)

            self._loguru_sink_id = loguru_logger.add(
                _tui_sink,
                level="INFO",
                format="{time:HH:mm:ss} | {level: <8} | {message}",
                colorize=False,
                enqueue=False,
            )

        def on_unmount(self) -> None:
            """移除 loguru TUI sink，避免 App 退出后 sink 仍持有引用。
            强制退出路径（Q键/强制中止）：调用 os._exit(0) 立即杀进程，
            避免 asyncio.to_thread 里的 GPU 工作线程阻塞退出。
            """
            # 恢复 sys.stderr
            if hasattr(self, "_original_stderr") and self._original_stderr is not None:
                sys.stderr = self._original_stderr
                self._original_stderr = None
            if self._loguru_sink_id is not None:
                from loguru import logger as loguru_logger
                try:
                    loguru_logger.remove(self._loguru_sink_id)
                except Exception:
                    pass
                self._loguru_sink_id = None
            if self._force_exit:
                import os, sys
                sys.stdout.write("\033[2J\033[H")
                sys.stdout.flush()
                os._exit(0)

        def _append_to_log_area(self, text: str) -> None:
            """将文本追加到 LogArea（在 Textual 主线程中调用）。"""
            try:
                log_area = self.query_one("#log-area", LogArea)
                log_area.append_log("", "sys", text)
            except Exception:
                pass

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal():
                yield PipelineSidebar(id="sidebar")
                yield MainArea(id="main-area")
            yield LogArea(id="log-area")
            yield Footer()

        def handle_pipeline_event(self, event: "PipelineEvent") -> None:
            """处理来自 PipelineSession 的事件（在 Textual 主线程中调用）。"""
            try:
                sidebar = self.query_one("#sidebar", PipelineSidebar)
                main_area = self.query_one("#main-area", MainArea)
                log_area = self.query_one("#log-area", LogArea)
            except Exception:
                return

            match event.type:
                case "stage_enter":
                    sidebar.update_node_status(event.node_id, "running")
                    main_area.show_stage_progress(event.node_id)
                case "stage_exit":
                    status = "completed" if event.status == "success" else "failed"
                    sidebar.update_node_status(event.node_id, status)
                    main_area.show_stage_summary(
                        event.node_id,
                        event.summary,
                        elapsed_sec=getattr(event, "elapsed_sec", 0.0),
                    )
                case "progress":
                    main_area.handle_node_progress(event)
                case "log":
                    log_area.append_log(event.level, event.node_id, event.message)
                case "need_input":
                    main_area.show_review_screen(event.display, self._session.send_action)
                case "error":
                    main_area.show_error(event.node_id, event.error)
                    log_area.append_log("ERROR", event.node_id, event.error)
                case "paused":
                    if self._graceful_quit:
                        # 优雅退出：等待当前阶段完成后，流水线暂停，此时直接退出
                        self._force_exit = True
                        self.exit()
                    else:
                        self.push_screen(PauseDialog(session=self._session))
                case "pipeline_complete":
                    main_area.show_complete(event.output)

        def action_pause(self) -> None:
            """P 键触发暂停对话框。"""
            self.push_screen(PauseDialog(session=self._session))

        def action_show_log(self) -> None:
            """L 键推出全屏日志界面，Esc 返回。"""
            self.push_screen(LogScreen())

        def action_quit_app(self) -> None:
            """Q 键弹出退出对话框，让用户选择退出方式。"""
            self.push_screen(QuitDialog(session=self._session))

    # -----------------------------------------------------------------------
    # PipelineSidebar
    # -----------------------------------------------------------------------

    class PipelineSidebar(Widget):
        """侧边栏：显示 8 个节点的运行状态。"""

        _NODE_LABELS: dict[str, str] = {
            "l1a_asr":          "L1A ASR",
            "l1b_align":        "L1B 对齐",
            "l3_precompute":    "L3 预计算",
            "l2a_comprehension":"L2A 理解",
            "l2b_decision":     "L2B 决策",
            "l2c_review":       "L2C 审核",
            "l2d_human":        "L2D 人工",
            "l3_execute":       "L3 执行",
        }
        _STATUS_ICONS: dict[str, str] = {
            "pending":   "○",
            "running":   "→",
            "completed": "✓",
            "failed":    "✗",
            "skipped":   "⊘",
        }

        def compose(self) -> ComposeResult:
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
    # GenericStageView
    # -----------------------------------------------------------------------

    class GenericStageView(Widget):
        """通用阶段视图：用 RichLog 替代 Static，消除溢出问题。

        非 L1A 节点使用此视图。冻结行写入 RichLog（自动滚动），
        当前进度行用底部 Static 原地刷新，不再需要手动截断逻辑。
        """

        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self._current_text: str = ""

        def compose(self) -> ComposeResult:
            yield RichLog(id="generic-log", max_lines=200, wrap=True, auto_scroll=True)
            yield Static("", id="generic-current")

        def append_frozen(self, line: str) -> None:
            """将一行文本写入 RichLog（冻结历史行）。"""
            if line:
                try:
                    self.query_one("#generic-log", RichLog).write(line)
                except Exception:
                    pass

        def set_current(self, line: str) -> None:
            """更新底部当前进度行 Static。"""
            self._current_text = line
            try:
                self.query_one("#generic-current", Static).update(line)
            except Exception:
                pass

        def freeze_current(self) -> None:
            """将当前进度行冻结到 RichLog，然后清空 Static。"""
            if self._current_text:
                self.append_frozen(self._current_text)
            self.set_current("")

    # -----------------------------------------------------------------------
    # L1aProgressView
    # -----------------------------------------------------------------------

    class L1aProgressView(Widget):
        """L1A 专用进度视图：三区分离（进度条 / 识别文本 / 块状态）。

        进度区（#l1a-progress-bar）：仅由 asr_intra_chunk_progress 更新，
            显示进度条 + 百分比 + 剩余时间 + 速度，**不写识别文本**。
        识别文本区（#l1a-text-log）：仅由 asr_chunk_done 写入，
            每块完成后追加完整识别文本，**不更新进度条**。
            使用 RichLog + 全量重渲染，支持 resize 时宽度自适应。
        块状态区（#l1a-chunk-status）：其余所有 phase 更新此区域。
        """

        def compose(self) -> ComposeResult:
            yield Static("", id="l1a-progress-bar")   # 进度区，固定 2 行
            yield RichLog(id="l1a-text-log", max_lines=200, wrap=True, auto_scroll=True)  # 识别文本区
            yield Static("", id="l1a-chunk-status")   # 块状态区，固定 1 行

        def on_mount(self) -> None:
            self._texts: list[str] = []

        def on_resize(self) -> None:
            """终端宽度变化时重渲染识别文本，确保换行宽度跟着更新。"""
            self._redraw_text_log()

        def _redraw_text_log(self) -> None:
            """全量重渲染识别文本区：历史行默认色，最新行绿色。"""
            try:
                log = self.query_one("#l1a-text-log", RichLog)
                log.clear()
                if not self._texts:
                    return
                for text in self._texts[:-1]:
                    log.write(text)
                # 最新行用绿色高亮
                log.write(f"[green]{self._texts[-1]}[/green]")
                log.scroll_end(animate=False)
            except Exception:
                pass

        def handle_progress(self, event: "PipelineEvent") -> None:
            """按 event.phase 分发到对应区域。所有 payload 解析通过视图模型函数完成。"""
            phase = event.phase
            payload = event.payload

            if phase == "asr_intra_chunk_progress":
                # 仅更新进度区，不写识别文本区
                s = _parse_l1a_intra_chunk_progress(payload)
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
                # 仅写识别文本区，不更新进度条
                s = _parse_l1a_chunk_done(payload)
                if s.text:
                    self._texts.append(s.text)
                    self._redraw_text_log()
                try:
                    self.query_one("#l1a-chunk-status", Static).update("")
                except Exception:
                    pass

            else:
                # 其余所有 phase → 更新块状态区
                status_line = _format_progress(event.node_id, event.phase, event.payload)
                try:
                    self.query_one("#l1a-chunk-status", Static).update(status_line)
                except Exception:
                    pass

    # -----------------------------------------------------------------------
    # MainArea
    # -----------------------------------------------------------------------

    class MainArea(Widget):
        """主区域：根据活跃节点切换视图。

        - GenericStageView（默认）：非 L1A 节点使用，RichLog 承载历史行，底部 Static 显示当前进度
        - L1aProgressView（L1A 专用）：三区分离，进度条 / 识别文本 / 块状态各自独立更新
        - ReviewScreen（L2D 人工审阅）：need_input 时挂载，覆盖主区域
        """

        def compose(self) -> ComposeResult:
            yield GenericStageView(id="generic-view")

        def show_stage_progress(self, node_id: str) -> None:
            """节点开始：按 node_id 切换视图。"""
            if node_id == "l1a_asr":
                self._switch_to_l1a_view()
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
            """节点完成：追加完成摘要，切回通用视图。"""
            elapsed_str = f" ({elapsed_sec:.1f}s)" if elapsed_sec > 0 else ""
            summary_line = f"✓ {node_id}{elapsed_str}"
            if node_id == "l1a_asr":
                self._teardown_l1a_view(summary_line)
            else:
                try:
                    gv = self.query_one("#generic-view", GenericStageView)
                    gv.freeze_current()
                    gv.append_frozen(summary_line)
                    gv.set_current("")
                except Exception:
                    pass

        def handle_node_progress(self, event: "PipelineEvent") -> None:
            """处理结构化 ProgressEvent，按 node_id 分发到对应视图。"""
            if event.node_id == "l1a_asr":
                try:
                    self.query_one("#l1a-view", L1aProgressView).handle_progress(event)
                except Exception:
                    pass
            else:
                try:
                    gv = self.query_one("#generic-view", GenericStageView)
                    gv.set_current(_format_progress(event.node_id, event.phase, event.payload))
                except Exception:
                    pass

        def _switch_to_l1a_view(self) -> None:
            """隐藏通用视图，挂载 L1A 专用视图。"""
            try:
                gv = self.query_one("#generic-view", GenericStageView)
                gv.display = False
            except Exception:
                pass
            # 若已存在则不重复挂载
            existing = self.query("#l1a-view")
            if not existing:
                try:
                    self.mount(L1aProgressView(id="l1a-view"))
                except Exception:
                    pass

        def _teardown_l1a_view(self, summary_line: str) -> None:
            """卸载 L1A 视图，恢复通用视图，追加摘要行。"""
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
            """确保通用视图可见。"""
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
        """日志区域：可滚动，显示最近 100 条 log 事件。"""

        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            # LogScreen 打开时持有对其 RichLog 的引用，用于实时同步新日志
            self._log_screen_ref: "RichLog | None" = None

        def compose(self) -> ComposeResult:
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
            # 若 LogScreen 当前打开，同步写入全屏日志
            if self._log_screen_ref is not None:
                try:
                    self._log_screen_ref.write(text)
                except Exception:
                    self._log_screen_ref = None

    # -----------------------------------------------------------------------
    # ReviewScreen（Widget，嵌入 MainArea）
    # -----------------------------------------------------------------------

    class ReviewScreen(Widget):
        """2d 人工审阅界面，嵌入主区域。

        复用 intelligence_2d_shell.py 中的格式化函数，
        通过 on_action 回调调用 session.send_action()。
        """

        def __init__(
            self,
            display: "DisplayData | None",
            on_action: Callable,
            **kwargs,
        ) -> None:
            super().__init__(**kwargs)
            self._display = display
            self._on_action = on_action

        def compose(self) -> ComposeResult:
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
            """刷新显示内容（使用 tui_adapter 中的格式化函数）。"""
            dd = self._display
            if dd is None:
                return
            try:
                self.query_one("#goal-header", Static).update(
                    f"目标: {dd.goal}\n主旨: {dd.comprehension.get('purpose', '')}"
                )
                self.query_one("#review-summary", Static).update(
                    _format_review_summary(dd.review_report)
                )
                self.query_one("#decision-list", Static).update(
                    _format_decision_list(dd)
                )
                self.query_one("#stats-bar", Static).update(
                    _format_stats(dd.stats)
                )
            except Exception as e:
                logger.warning("ReviewScreen._refresh_display 失败: %s", e)

        def update_display(self, display: "DisplayData") -> None:
            """更新展示数据并刷新界面（由 NeedInputEvent 触发）。"""
            self._display = display
            self._refresh_display()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            """解析命令并通过 on_action 回调传递给 PipelineSession。"""
            from autosmartcut.intelligence_2d_shell import HELP_TEXT, parse_command

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
    # LogScreen（全屏日志界面，push_screen 推入，Esc 返回）
    # -----------------------------------------------------------------------

    class LogScreen(Screen):
        """全屏日志界面。

        通过 L 键从主界面推入（push_screen），Esc 键弹出返回（pop_screen）。
        日志内容与底部 LogArea 共享同一个 RichLog 实例——LogScreen 挂载时把
        RichLog 从 LogArea 移过来，卸载时再移回去，保证日志不丢失、不重复。

        注意：Textual 不允许同一个 Widget 实例同时挂载在两处，因此采用"借用"
        而非"复制"的方式。若借用失败（LogArea 尚未就绪），则降级为独立 RichLog
        并在 LogArea 上追加一条提示。
        """

        BINDINGS = [Binding("escape", "app.pop_screen", "返回", show=True)]

        def compose(self) -> ComposeResult:
            yield Header()
            yield RichLog(id="log-screen-rich", max_lines=2000, wrap=True)
            yield Footer()

        def on_mount(self) -> None:
            """挂载时把 LogArea 中已有的日志内容复制到本屏的 RichLog。"""
            try:
                log_area = self.app.query_one("#log-area", LogArea)
                src = log_area.query_one("#log-rich", RichLog)
                dst = self.query_one("#log-screen-rich", RichLog)
                # RichLog 没有公开"导出所有行"的 API，通过内部 _lines 读取
                # 若未来 Textual 版本移除该属性，此处静默跳过，不影响功能
                lines = getattr(src, "_lines", None)
                if lines:
                    for line in lines:
                        dst.write(line)
                dst.scroll_end(animate=False)
                # 将后续新日志同步写入本屏（通过 LogArea.append_log 的钩子）
                log_area._log_screen_ref = dst
            except Exception as e:
                logger.warning("LogScreen.on_mount 复制日志失败: %s", e)

        def on_unmount(self) -> None:
            """卸载时清除 LogArea 上的屏幕引用，停止同步。"""
            try:
                log_area = self.app.query_one("#log-area", LogArea)
                log_area._log_screen_ref = None
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # QuitDialog（Q 键触发，退出选项）
    # -----------------------------------------------------------------------

    class QuitDialog(Screen):
        """退出对话框：提供四种退出方式。"""

        def __init__(self, session: "PipelineSession", **kwargs) -> None:
            super().__init__(**kwargs)
            self._session = session

        def compose(self) -> ComposeResult:
            yield Label("退出选项：")
            yield Button("取消（继续执行）", id="btn-cancel", variant="default")
            yield Button("强制退出（不保存）", id="btn-force-quit", variant="error")
            yield Button("保存并退出", id="btn-save-quit", variant="warning")
            yield Button(
                "等待当前阶段完成后退出",
                id="btn-graceful-quit",
                variant="primary",
            )

        def on_button_pressed(self, event: Button.Pressed) -> None:
            match event.button.id:
                case "btn-cancel":
                    self.app.pop_screen()
                case "btn-force-quit":
                    self.app._force_exit = True
                    self._session.abort(save=False)
                    self.app.exit()
                case "btn-save-quit":
                    self.app._force_exit = True
                    self._session.abort(save=True)
                    self.app.exit()
                case "btn-graceful-quit":
                    # 设置暂停标志，当前节点完成后流水线自然停止
                    # pipeline_complete 或 paused 事件触发后 TUI 会自动退出
                    self._session.pause()
                    self.app._graceful_quit = True
                    self.app.pop_screen()

    # -----------------------------------------------------------------------
    # PauseDialog
    # -----------------------------------------------------------------------

    class PauseDialog(Screen):
        """暂停对话框，提供三个选项。"""

        def __init__(self, session: "PipelineSession", **kwargs) -> None:
            super().__init__(**kwargs)
            self._session = session

        def compose(self) -> ComposeResult:
            yield Label("流水线暂停选项：")
            yield Button("取消（继续执行）", id="btn-cancel", variant="default")
            yield Button("强制中止并保存", id="btn-abort-save", variant="warning")
            yield Button(
                "等待当前阶段完成后暂停",
                id="btn-pause-graceful",
                variant="primary",
            )

        def on_button_pressed(self, event: Button.Pressed) -> None:
            match event.button.id:
                case "btn-cancel":
                    self.app.pop_screen()
                case "btn-abort-save":
                    self.app._force_exit = True
                    self._session.abort(save=True)
                    self.app.exit()
                case "btn-pause-graceful":
                    self._session.pause()
                    self.app.pop_screen()

else:
    # Textual 不可用时提供占位类，避免 ImportError
    class PipelineApp:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise RuntimeError("Textual 未安装，无法使用 TUI 模式")

    class PipelineSidebar:  # type: ignore[no-redef]
        pass

    class MainArea:  # type: ignore[no-redef]
        pass

    class LogArea:  # type: ignore[no-redef]
        pass

    class ReviewScreen:  # type: ignore[no-redef]
        pass

    class PauseDialog:  # type: ignore[no-redef]
        pass
