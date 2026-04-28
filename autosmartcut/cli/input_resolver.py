"""input_resolver.py — 输入路径类型判断服务层。

提供 ``resolve_input()`` 函数，判断用户提供的路径是媒体文件、清单文件还是清单目录，
并在已有工程的情况下附带进度报告。

不依赖任何 CLI 框架，可被 CLI、TUI、GUI 直接调用。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autosmartcut.manifest.manifest_progress import ProgressReport


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 支持的媒体文件扩展名（小写）
MEDIA_EXTENSIONS: frozenset[str] = frozenset({
    ".mp4", ".mkv", ".mov", ".avi", ".webm",
    ".ts", ".m4v", ".flv", ".wmv", ".m2ts",
})


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

class InputType(Enum):
    """输入路径类型。"""
    MEDIA_FILE = "media"
    """媒体文件（.mp4 / .mkv / ...），用于新建工程。"""

    MANIFEST_FILE = "manifest"
    """timeline_manifest.json 文件，用于续跑已有工程。"""

    MANIFEST_DIR = "dir"
    """包含 timeline_manifest.json 的目录，用于续跑已有工程。"""


@dataclass
class ResolvedInput:
    """输入路径解析结果。"""
    input_type: InputType
    path: Path
    """原始输入路径（已 resolve 为绝对路径）。"""

    manifest_path: Path | None
    """清单文件绝对路径。MANIFEST_FILE / MANIFEST_DIR 时有值，MEDIA_FILE 时为 None。"""

    media_path: Path | None
    """媒体文件绝对路径。MEDIA_FILE 时有值，其他时为 None。"""

    progress_report: "ProgressReport | None"
    """进度报告。MANIFEST_FILE / MANIFEST_DIR 时有值，MEDIA_FILE 时为 None。"""


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------

def resolve_input(path: Path) -> ResolvedInput:
    """判断输入路径类型，如果是已有工程则附带进度报告。

    判断逻辑：
    1. 路径是文件 + 扩展名在 MEDIA_EXTENSIONS → MEDIA_FILE
    2. 路径是文件 + 扩展名 .json → 尝试 load_manifest 验证 → MANIFEST_FILE
    3. 路径是目录 → 检查 path/timeline_manifest.json → MANIFEST_DIR
    4. 其他 → ValueError

    Args:
        path: 用户提供的路径（文件或目录）。

    Returns:
        ResolvedInput 解析结果。

    Raises:
        FileNotFoundError: 路径不存在。
        ValueError: 路径类型无法识别，或 .json 文件不是有效清单。
    """
    p = Path(path).resolve()

    if not p.exists():
        raise FileNotFoundError(f"路径不存在: {p}")

    if p.is_file():
        suffix = p.suffix.lower()

        # 媒体文件
        if suffix in MEDIA_EXTENSIONS:
            return ResolvedInput(
                input_type=InputType.MEDIA_FILE,
                path=p,
                manifest_path=None,
                media_path=p,
                progress_report=None,
            )

        # JSON 文件 → 尝试作为清单
        if suffix == ".json":
            return _resolve_manifest_file(p)

        raise ValueError(
            f"无法识别的文件类型: {p.suffix!r}。"
            f"支持的媒体格式: {', '.join(sorted(MEDIA_EXTENSIONS))}；"
            f"或提供 timeline_manifest.json"
        )

    if p.is_dir():
        return _resolve_manifest_dir(p)

    raise ValueError(f"路径既不是文件也不是目录: {p}")


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _resolve_manifest_file(json_path: Path) -> ResolvedInput:
    """验证 JSON 文件是否为有效清单，并推断进度。"""
    from autosmartcut.manifest.manifest_io import load_manifest
    from autosmartcut.manifest.manifest_progress import infer_progress

    try:
        data = load_manifest(json_path)
    except Exception as e:
        raise ValueError(f"无法读取清单文件 {json_path}: {e}") from e

    # 简单验证：必须有 version 字段
    if "version" not in data:
        raise ValueError(
            f"{json_path} 不是有效的 timeline_manifest.json（缺少 version 字段）"
        )

    report = infer_progress(data, json_path)
    return ResolvedInput(
        input_type=InputType.MANIFEST_FILE,
        path=json_path,
        manifest_path=json_path,
        media_path=None,
        progress_report=report,
    )


def _resolve_manifest_dir(dir_path: Path) -> ResolvedInput:
    """在目录中查找 timeline_manifest.json 并推断进度。"""
    from autosmartcut.manifest.manifest_io import MANIFEST_FILENAME

    candidate = dir_path / MANIFEST_FILENAME
    if not candidate.is_file():
        raise FileNotFoundError(
            f"目录 {dir_path} 中找不到 {MANIFEST_FILENAME}"
        )

    result = _resolve_manifest_file(candidate)
    # 保留原始目录路径，manifest_path 指向具体文件
    return ResolvedInput(
        input_type=InputType.MANIFEST_DIR,
        path=dir_path,
        manifest_path=result.manifest_path,
        media_path=None,
        progress_report=result.progress_report,
    )
