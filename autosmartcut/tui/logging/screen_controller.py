"""日志屏：历史加载 + 实时追加 + follow 滚动策略（不依赖 Textual 类型）。"""

from __future__ import annotations

from typing import Any, Callable

from autosmartcut.tui.logging.context import RunLogContext
from autosmartcut.tui.logging.repository import LogRepository
from autosmartcut.tui.logging.stream_hub import LogStreamHub


class LogScreenController:
    """控制日志全屏：先读历史，再订阅 Hub；仅在底部附近时自动追尾。

    生命周期：
    1. ``attach()``        — 绑定 UI 控件（不加载历史，不订阅 Hub）
    2. ``load_history_lines_sync()`` — 同步读文件，供 asyncio.to_thread 调用
    3. ``set_history_meta()``        — 历史写入完成后更新元数据
    4. ``subscribe_live()``          — 订阅 Hub 实时流（历史写完后调用，保证顺序）
    5. ``detach()``        — 卸载时取消订阅
    """

    def __init__(
        self,
        hub: LogStreamHub,
        repository: LogRepository,
        context: RunLogContext,
        *,
        max_history_files: int = 50,
        max_history_lines: int = 200_000,
        bottom_epsilon: int = 2,
        schedule_flush: Callable[[], None] | None = None,
    ) -> None:
        self._hub = hub
        self._repository = repository
        self._context = context
        self._max_history_files = max_history_files
        self._max_history_lines = max_history_lines
        self._bottom_epsilon = bottom_epsilon
        self._schedule_flush = schedule_flush

        self._rich_log: Any | None = None
        self._status: Any | None = None
        self._hub_token: int | None = None
        self._is_suppressed: Callable[[], bool] = lambda: False

        self._follow: bool = True
        self._suppress_follow_detection: bool = False
        self._live_line_count: int = 0
        self._history_files: int = 0
        self._history_lines: int = 0

        # 50ms batch 节流
        self._live_pending: list[str] = []
        self._live_flush_scheduled: bool = False

    def attach(
        self,
        *,
        rich_log: Any,
        status_static: Any,
        is_suppressed: Callable[[], bool],
    ) -> None:
        """绑定 UI 控件。不加载历史，不订阅 Hub。

        历史加载由 LogScreen.on_mount 异步完成后调用 set_history_meta()，
        实时订阅由 subscribe_live() 在历史写入完成后调用，保证顺序。
        """
        self._rich_log = rich_log
        self._status = status_static
        self._is_suppressed = is_suppressed

    def load_history_lines_sync(self) -> tuple[list[str], int, int]:
        """同步读取历史行。供 asyncio.to_thread 调用（不在主线程执行）。

        Returns:
            (lines, files_read, line_count)
        """
        return self._repository.load_history_lines(
            self._context,
            max_files=self._max_history_files,
            max_total_lines=self._max_history_lines,
        )

    def set_history_meta(self, n_files: int, n_lines: int) -> None:
        """设置历史元数据（主线程调用，历史写入完成后）。"""
        self._history_files = n_files
        self._history_lines = n_lines
        self._live_line_count = 0
        self._update_status()

    def subscribe_live(self) -> None:
        """订阅 Hub 实时流。历史加载完成后调用，保证顺序。"""
        if self._hub_token is None:
            self._hub_token = self._hub.subscribe(self._on_live_line)
        self._update_status()

    def begin_batch_write(self) -> None:
        """批量写入开始前调用，暂停 follow 检测。"""
        self._suppress_follow_detection = True

    def end_batch_write(self) -> None:
        """批量写入结束后调用，恢复 follow 检测。"""
        self._suppress_follow_detection = False

    def detach(self) -> None:
        if self._hub_token is not None:
            self._hub.unsubscribe(self._hub_token)
            self._hub_token = None
        self._rich_log = None
        self._status = None

    def set_follow(self, enabled: bool) -> None:
        self._follow = enabled
        if enabled and self._rich_log is not None:
            self._suppress_follow_detection = True
            try:
                self._rich_log.scroll_end(animate=False)
            finally:
                self._suppress_follow_detection = False
        self._update_status()

    def toggle_follow(self) -> None:
        self.set_follow(not self._follow)

    def notify_scroll_y_changed(self, _old: object, _new: object) -> None:
        if self._suppress_follow_detection or self._is_suppressed():
            return
        if not self._follow:
            return
        if not self._is_near_bottom():
            self._follow = False
            self._update_status()

    def notify_user_scroll_up(self) -> None:
        if self._suppress_follow_detection or self._is_suppressed():
            return
        self._follow = False
        self._update_status()

    def flush_live_pending(self) -> None:
        """由 LogScreen 的 timer 回调调用，批量写入积压的实时日志行。"""
        self._live_flush_scheduled = False
        if not self._live_pending:
            return
        lines, self._live_pending = self._live_pending, []
        rl = self._rich_log
        if rl is None:
            return
        at_bottom = self._is_near_bottom()
        for line in lines:
            try:
                rl.write(line)
            except Exception:
                pass
        if self._follow and at_bottom:
            self._suppress_follow_detection = True
            try:
                rl.scroll_end(animate=False)
            finally:
                self._suppress_follow_detection = False
        self._update_status()

    def _is_near_bottom(self) -> bool:
        rl = self._rich_log
        if rl is None:
            return True
        try:
            sy = getattr(rl, "scroll_y", None)
            ms = getattr(rl, "max_scroll_y", None)
            if sy is None or ms is None:
                return True
            return (int(ms) - int(sy)) <= self._bottom_epsilon
        except Exception:
            return True

    def _on_live_line(self, line: str) -> None:
        """Hub 回调：积攒行，50ms 后批量写入（节流）。"""
        self._live_line_count += 1
        self._live_pending.append(line)
        if not self._live_flush_scheduled:
            self._live_flush_scheduled = True
            if self._schedule_flush is not None:
                self._schedule_flush()

    def _update_status(self) -> None:
        st = self._status
        if st is None:
            return
        follow_txt = "ON" if self._follow else "OFF"
        try:
            st.update(
                f"跟随 F: {follow_txt}  |  历史文件: {self._history_files}  "
                f"历史行: {self._history_lines}  |  本会话实时: {self._live_line_count}  "
                f"|  End 底部  Esc 返回"
            )
        except Exception:
            pass
