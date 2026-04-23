from __future__ import annotations

import logging
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

from autosmartcut.backends.smartcut_backend import SmartcutRenderOptions, render_segments
from autosmartcut.l3_models import L3FinalPlan, ResolvedTask

logger = logging.getLogger(__name__)


def assemble_from_tile_clips(*, output_video: Path, clip_paths: list[Path]) -> Path:
    """使用 ffmpeg concat demuxer 无损拼接分片（需 ``ffmpeg`` 在 PATH）。"""
    if not clip_paths:
        raise ValueError("assemble_from_tile_clips: clip_paths 为空")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，无法执行分片无损拼接")
    output_video.parent.mkdir(parents=True, exist_ok=True)
    list_path = output_video.parent / "_ascut_concat_list.txt"
    lines: list[str] = []
    for p in clip_paths:
        fp = str(p.resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{fp}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        str(output_video),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "ffmpeg concat 失败")
    logger.info("[L3] 分片拼接完成 → %s", output_video)
    return output_video


def assemble_output(
    *,
    plan: L3FinalPlan,
    positive_segments: list[tuple[Fraction, Fraction]],
    resolved_tasks: list[ResolvedTask],
) -> Path:
    """当前版本统一走 smartcut 全量切段输出。

    这里保留 resolved_tasks 参数是为了后续命中缓存拼装能力的无缝接入。
    """
    _ = resolved_tasks
    render_segments(
        source_video=plan.source_video,
        output_video=plan.output_video,
        positive_segments=positive_segments,
        options=SmartcutRenderOptions(),
    )
    return plan.output_video

