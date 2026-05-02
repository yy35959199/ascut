"""历史 run_*.log 发现与读取（与 Textual 无关）。"""

from __future__ import annotations

from pathlib import Path

from autosmartcut.tui.logging.context import RunLogContext


class LogRepository:
    """在清单/日志目录下扫描 run_*.log 并按时间顺序读取内容。"""

    def log_directory(self, ctx: RunLogContext) -> Path:
        if ctx.active_log_path is not None:
            return ctx.active_log_path.parent
        return ctx.manifest_path.parent

    def list_run_log_files(self, ctx: RunLogContext) -> list[Path]:
        """按文件名排序的 run_*.log 列表；若存在活动日志则只包含到该文件为止（含）。"""
        d = self.log_directory(ctx)
        if not d.is_dir():
            return []
        files = sorted(d.glob("run_*.log"), key=lambda p: p.name)
        if ctx.active_log_path is not None:
            try:
                active = ctx.active_log_path.resolve()
                idx = next(
                    (i for i, p in enumerate(files) if p.resolve() == active),
                    None,
                )
                if idx is not None:
                    files = files[: idx + 1]
            except OSError:
                pass
        return files

    def load_history_lines(
        self,
        ctx: RunLogContext,
        *,
        max_files: int = 50,
        max_total_lines: int = 200_000,
    ) -> tuple[list[str], int, int]:
        """读取历史行。

        Returns:
            (lines, files_read, line_count)
        """
        files = self.list_run_log_files(ctx)[-max_files:]
        out: list[str] = []
        files_read = 0
        for fp in files:
            if not fp.is_file():
                continue
            files_read += 1
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for raw in text.splitlines():
                if len(out) >= max_total_lines:
                    return out, files_read, len(out)
                out.append(raw)
        return out, files_read, len(out)
