"""本次运行日志上下文（与 Textual 无关）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autosmartcut.cli.app_controller import AppController


@dataclass(frozen=True)
class RunLogContext:
    """定位清单目录、当前活动日志文件与 run_id，供历史加载使用。"""

    manifest_path: Path
    """清单路径；未知时可为占位路径。"""

    active_log_path: Path | None
    """当前 loguru 文件 sink 对应的 run_*.log；无文件 sink 时为 None。"""

    output_dir: Path | None
    """PipelineRun.output_dir；诊断阶段尚无 run 时可为 None。"""

    run_id: str | None
    """清单中的 run_id；无法读取时为 None。"""

    @staticmethod
    def from_app_controller(ctrl: "AppController") -> "RunLogContext":
        """从 AppController 构造上下文（TUI 启动后任意时刻可调用）。"""
        from autosmartcut.log import get_active_log_path
        from autosmartcut.manifest.manifest_io import load_manifest

        manifest: Path | None = None
        output_dir: Path | None = None
        run_id: str | None = None

        run = getattr(ctrl, "_run", None)
        if run is not None:
            manifest = Path(run.manifest_path).resolve()
            output_dir = Path(run.output_dir).resolve()
            run_id = str(run.run_id)

        if manifest is None:
            ri = getattr(ctrl, "_resolved_input", None)
            mp = getattr(ri, "manifest_path", None) if ri is not None else None
            if mp is not None:
                manifest = Path(mp).resolve()
                if manifest.is_file():
                    try:
                        data = load_manifest(manifest)
                        run_id = str(data.get("run_id") or "") or None
                    except (OSError, ValueError):
                        run_id = None

        if manifest is None:
            manifest = Path.cwd() / "timeline_manifest.json"

        active = get_active_log_path()
        return RunLogContext(
            manifest_path=manifest,
            active_log_path=active,
            output_dir=output_dir,
            run_id=run_id,
        )
