"""Layer 2 / 2d TUI Shell — Textual 终端交互界面

## 职责
基于 Textual 框架的终端交互界面，包含 ReviewScreen 和 LogScreen 两个 Screen。
负责命令解析、UI 渲染、Screen 切换。调用 Core API 完成所有业务逻辑。

## 接口
- parse_command(raw_input) → Action | str | None
- run_2d_interactive(manifest_dict) → (manifest_dict, Signal)
"""

from __future__ import annotations

import logging
from typing import Any

from autosmartcut.nodes.l2.intelligence_2d_core import (
    AcceptAction,
    Action,
    BatchToggleAction,
    CoreResult,
    DisplayData,
    FeedbackAction,
    FeedbackType,
    QuitAction,
    ShowAction,
    Signal,
    ToggleAction,
    run_2d,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 命令解析
# ============================================================================

def parse_command(raw_input: str) -> Action | str | None:
    """将用户输入的文本命令解析为 Action 对象。

    命令格式：
        t <index>              → ToggleAction(index)
        f1 <text>              → FeedbackAction(F1_PURPOSE_DRIFT, {"text": text})
        f2 <idx> <old> <new>   → FeedbackAction(F2_KEYWORD_ERROR, {"index": idx, "old": old, "new": new})
        f3 <text>              → FeedbackAction(F3_SELECTION_OPINION, {"text": text})
        f4 <idx,idx,...>       → FeedbackAction(F4_TIME_POINT, {"indices": [...]})
        a                      → AcceptAction()
        q                      → QuitAction()
        L                      → "show_log" (特殊字符串，由 TUI 处理)
        ? / help               → "show_help" (特殊字符串)

    无法解析时返回 None，不抛异常。
    """
    if not isinstance(raw_input, str):
        return None

    cmd = raw_input.strip()
    if not cmd:
        return None

    # 单字符 / 特殊命令
    if cmd == "a":
        return AcceptAction()
    if cmd == "q":
        return QuitAction()
    if cmd == "L":
        return "show_log"
    if cmd in ("?", "help"):
        return "show_help"

    # t <index>
    if cmd.startswith("t "):
        try:
            index = int(cmd[2:].strip())
            if index < 0:
                return None
            return ToggleAction(index=index)
        except (ValueError, IndexError):
            return None

    # f1 <text>
    if cmd.startswith("f1 "):
        text = cmd[3:].strip()
        if not text:
            return None
        return FeedbackAction(
            feedback_type=FeedbackType.F1_PURPOSE_DRIFT,
            payload={"text": text},
        )

    # f2 <idx> <old> <new>
    if cmd.startswith("f2 "):
        parts = cmd[3:].strip().split(None, 2)  # 最多分 3 段
        if len(parts) < 3:
            return None
        try:
            idx = int(parts[0])
            if idx < 0:
                return None
        except ValueError:
            return None
        old = parts[1]
        new = parts[2]
        return FeedbackAction(
            feedback_type=FeedbackType.F2_KEYWORD_ERROR,
            payload={"index": idx, "old": old, "new": new},
        )

    # f3 <text>
    if cmd.startswith("f3 "):
        text = cmd[3:].strip()
        if not text:
            return None
        return FeedbackAction(
            feedback_type=FeedbackType.F3_SELECTION_OPINION,
            payload={"text": text},
        )

    # f4 <idx,idx,...>
    if cmd.startswith("f4 "):
        raw_indices = cmd[3:].strip()
        if not raw_indices:
            return None
        try:
            indices = [int(x.strip()) for x in raw_indices.split(",")]
            if any(i < 0 for i in indices):
                return None
            return FeedbackAction(
                feedback_type=FeedbackType.F4_TIME_POINT,
                payload={"indices": indices},
            )
        except ValueError:
            return None

    return None


# ============================================================================
# Textual TUI App
# ============================================================================

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Vertical, VerticalScroll
    from textual.screen import Screen
    from textual.widgets import (
        Footer,
        Header,
        Input,
        Label,
        RichLog,
        Static,
    )

    _TEXTUAL_AVAILABLE = True
except ImportError:
    _TEXTUAL_AVAILABLE = False


HELP_TEXT = """\
可用命令:
  t <index>            切换指定句子的保留/删除状态
  f1 <text>            主旨偏差反馈（回流 2a）
  f2 <idx> <old> <new> 关键词纠错（回流 2a）
  f3 <text>            内容选择意见（回流 2b）
  f4 <idx,idx,...>     批量切换时间节点
  a                    确认当前决策
  q                    退出不保存
  L                    查看日志
  ? / help             显示帮助
"""


if _TEXTUAL_AVAILABLE:

    class ReviewScreen(Screen):
        """主审阅屏：决策列表 + 命令输入"""

        BINDINGS = [
            Binding("escape", "quit_app", "退出", show=False),
        ]

        def __init__(
            self,
            manifest_dict: dict[str, Any],
            display_data: DisplayData | None = None,
            **kwargs: Any,
        ) -> None:
            super().__init__(**kwargs)
            self.manifest_dict = manifest_dict
            self.display_data = display_data

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            with VerticalScroll(id="main-scroll"):
                yield Static(id="goal-header")
                yield Static(id="review-summary")
                yield Static(id="decision-list")
                yield Static(id="stats-bar")
                yield Static(id="message-bar")
            yield Input(placeholder="命令: t/f1/f2/f3/f4/a/q/L/?", id="cmd-input")
            yield Footer()

        def on_mount(self) -> None:
            if self.display_data is None:
                # 初始加载：通过 ShowAction 获取 display_data
                result = run_2d(self.manifest_dict, ShowAction())
                self.manifest_dict = result.manifest_dict
                self.display_data = result.display_data
            self._refresh_display()
            self.query_one("#cmd-input", Input).focus()

        def _refresh_display(self) -> None:
            dd = self.display_data
            if dd is None:
                return

            from autosmartcut.cli.formatters import (
                format_decision_list as _format_decision_list,
                format_review_summary as _format_review_summary,
                format_stats as _format_stats,
            )

            goal = dd.goal or ""
            purpose = dd.comprehension.get("purpose", "")
            self.query_one("#goal-header", Static).update(
                f"目标: {goal}\n主旨: {purpose}"
            )
            self.query_one("#review-summary", Static).update(
                _format_review_summary(dd.review_report)
            )
            self.query_one("#decision-list", Static).update(
                _format_decision_list(dd, use_markup=True)
            )
            self.query_one("#stats-bar", Static).update(
                _format_stats(dd.stats)
            )

        def on_input_submitted(self, event: Input.Submitted) -> None:
            """处理命令输入。"""
            cmd_input = self.query_one("#cmd-input", Input)
            raw = event.value.strip()
            cmd_input.value = ""

            if not raw:
                return

            parsed = parse_command(raw)

            if parsed == "show_help":
                self.query_one("#message-bar", Static).update(HELP_TEXT)
                return

            if parsed == "show_log":
                self.app.push_screen(LogScreen())
                return

            if parsed is None:
                self.query_one("#message-bar", Static).update(
                    f"无效命令: {raw}  (输入 ? 查看帮助)"
                )
                return

            # 调用 Core API
            result = run_2d(self.manifest_dict, parsed)
            self.manifest_dict = result.manifest_dict

            if result.signal == Signal.CONTINUE:
                self.display_data = result.display_data
                self._refresh_display()
                if result.message:
                    self.query_one("#message-bar", Static).update(result.message)

            elif result.signal == Signal.DONE:
                self.app._result_manifest = result.manifest_dict
                self.app._result_signal = Signal.DONE
                self.app.exit()

            elif result.signal == Signal.QUIT:
                self.app._result_manifest = result.manifest_dict
                self.app._result_signal = Signal.QUIT
                self.app.exit()

            elif result.signal in (Signal.REFLOW_2A, Signal.REFLOW_2B):
                # 返回信号给编排器，TUI 退出
                self.app._result_manifest = result.manifest_dict
                self.app._result_signal = result.signal
                self.app.exit()

        def action_quit_app(self) -> None:
            """Esc 退出。"""
            result = run_2d(self.manifest_dict, QuitAction())
            self.app._result_manifest = result.manifest_dict
            self.app._result_signal = Signal.QUIT
            self.app.exit()

    class LogScreen(Screen):
        """日志屏：全屏可滚动日志视图"""

        BINDINGS = [
            Binding("escape", "pop_screen", "返回审阅", show=True),
        ]

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield RichLog(id="log-view", wrap=True, highlight=True)
            yield Static("按 Esc 返回审阅屏", id="log-status")
            yield Footer()

        def on_mount(self) -> None:
            log_view = self.query_one("#log-view", RichLog)
            # 显示最近的日志记录
            log_view.write("日志视图 — 显示回流和 LLM 调用日志")
            log_view.write("按 Esc 返回审阅屏")

        def action_pop_screen(self) -> None:
            self.app.pop_screen()

    class ReviewApp(App):
        """2d 人工审阅 Textual App"""

        TITLE = "AutoSmartCut 2d 人工审阅"
        CSS = """
        #goal-header {
            background: $primary-background;
            padding: 1;
            margin-bottom: 1;
        }
        #review-summary {
            padding: 0 1;
            margin-bottom: 1;
        }
        #decision-list {
            padding: 0 1;
        }
        #stats-bar {
            background: $accent;
            padding: 0 1;
            margin-top: 1;
        }
        #message-bar {
            color: $warning;
            padding: 0 1;
        }
        #cmd-input {
            dock: bottom;
            margin-top: 1;
        }
        #log-view {
            height: 1fr;
        }
        #log-status {
            dock: bottom;
            background: $accent;
            padding: 0 1;
        }
        """

        def __init__(self, manifest_dict: dict[str, Any], **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._manifest_dict = manifest_dict
            self._result_manifest: dict[str, Any] = manifest_dict
            self._result_signal: Signal = Signal.DONE

        def on_mount(self) -> None:
            self.push_screen(ReviewScreen(self._manifest_dict))


# ============================================================================
# 入口函数
# ============================================================================

def run_2d_interactive(manifest_dict: dict[str, Any]) -> tuple[dict, Signal]:
    """2d TUI 交互入口：启动 Textual App，返回最终 manifest 和终止信号。

    Args:
        manifest_dict: 包含 tokens、keep_mask、comprehension 的工作数据

    Returns:
        (更新后的 manifest_dict, 终止信号)
        终止信号为 DONE / QUIT / REFLOW_2A / REFLOW_2B 之一
    """
    if not _TEXTUAL_AVAILABLE:
        # Textual 不可用，回退到简单 input() 循环
        return _run_2d_fallback(manifest_dict)

    try:
        app = ReviewApp(manifest_dict)
        app.run()
        return app._result_manifest, app._result_signal
    except EOFError:
        logger.info("[2d] 非交互模式，自动确认")
        result = run_2d(manifest_dict, AcceptAction())
        return result.manifest_dict, Signal.DONE


def _run_2d_fallback(manifest_dict: dict[str, Any]) -> tuple[dict, Signal]:
    """Textual 不可用时的简单 input() 回退循环。"""
    logger.info("[2d] Textual 不可用，使用简单交互模式")

    # 初始显示
    result = run_2d(manifest_dict, ShowAction())
    manifest_dict = result.manifest_dict

    if result.display_data:
        _print_display(result.display_data)

    while True:
        try:
            raw = input("\n命令 [t/f1/f2/f3/f4/a/q/?]: ").strip()
        except EOFError:
            logger.info("[2d] 非交互模式，自动确认")
            result = run_2d(manifest_dict, AcceptAction())
            return result.manifest_dict, Signal.DONE

        parsed = parse_command(raw)

        if parsed == "show_help":
            print(HELP_TEXT)
            continue

        if parsed == "show_log":
            print("（简单模式下无日志屏）")
            continue

        if parsed is None:
            print(f"无效命令: {raw}  (输入 ? 查看帮助)")
            continue

        result = run_2d(manifest_dict, parsed)
        manifest_dict = result.manifest_dict

        if result.signal == Signal.CONTINUE:
            if result.display_data:
                _print_display(result.display_data)
            if result.message:
                print(f"  → {result.message}")

        elif result.signal in (Signal.DONE, Signal.QUIT,
                               Signal.REFLOW_2A, Signal.REFLOW_2B):
            return manifest_dict, result.signal


def _print_display(dd: DisplayData) -> None:
    """简单模式下打印 DisplayData。"""
    from autosmartcut.cli.formatters import (
        format_decision_list as _format_decision_list,
        format_review_summary as _format_review_summary,
        format_stats as _format_stats,
    )
    print("\n" + "=" * 80)
    print(f"目标: {dd.goal}")
    print(f"主旨: {dd.comprehension.get('purpose', '')}")
    print("-" * 80)
    print(_format_review_summary(dd.review_report))
    print("-" * 80)
    print(_format_decision_list(dd))
    print("-" * 80)
    print(_format_stats(dd.stats))
