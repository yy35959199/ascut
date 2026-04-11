"""单次流水线运行的操作元信息（MVP-mini：以 timeline_manifest.json 为锚）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ulid import ULID

from autosmartcut.annotation_tokens import video_path_from_manifest
from autosmartcut.manifest_io import (
    MANIFEST_FILENAME,
    load_manifest,
    make_manifest_skeleton,
    save_manifest,
)


def _default_output_video(
    output_dir: Path, video_path: Path, output_video_name: str | None
) -> Path:
    if output_video_name:
        name = Path(output_video_name).name
        if not name or name in (".", ".."):
            raise ValueError(f"无效的输出视频文件名: {output_video_name!r}")
        return output_dir / name
    stem = video_path.stem
    suffix = video_path.suffix or ".mp4"
    return output_dir / f"{stem}_cut{suffix}"


@dataclass(frozen=True)
class PipelineRun:
    """贯穿 L1→L2→L3 的运行句柄；唯一持久化清单为 ``manifest_path``。"""

    run_id: str
    manifest_path: Path
    output_dir: Path
    output_video: Path
    goal: str
    started_at: datetime
    video_path: Path

    @property
    def log_path(self) -> Path:
        return self.output_dir / f"run_{self.run_id}.log"

    @classmethod
    def new(
        cls,
        video_path: Path,
        goal: str = "",
        output_dir: Path | None = None,
        output_video_name: str | None = None,
    ) -> PipelineRun:
        """从视频新建输出目录、写清单骨架、返回句柄。"""
        run_id = str(ULID())
        vp = video_path.resolve()
        if output_dir is None:
            od = vp.parent / f"ascut_out_{run_id[:8]}"
        else:
            od = Path(output_dir).resolve()
        od.mkdir(parents=True, exist_ok=True)
        mp = od / MANIFEST_FILENAME
        started = datetime.now()
        sk = make_manifest_skeleton(run_id, goal, str(vp))
        save_manifest(mp, sk, atomic=True)
        out = _default_output_video(od, vp, output_video_name)
        return cls(
            run_id=run_id,
            manifest_path=mp.resolve(),
            output_dir=od,
            output_video=out,
            goal=goal,
            started_at=started,
            video_path=vp,
        )

    @classmethod
    def from_manifest(
        cls,
        manifest_path: Path,
        *,
        goal_override: str | None = None,
        output_dir: Path | None = None,
        output_video_name: str | None = None,
    ) -> PipelineRun:
        """续跑：使用已有清单（不拷贝）。"""
        mp = manifest_path.resolve()
        if not mp.is_file():
            raise FileNotFoundError(f"找不到清单: {mp}")
        data = load_manifest(mp)
        rid = str(data.get("run_id") or ULID())
        od = Path(output_dir).resolve() if output_dir else mp.parent.resolve()
        od.mkdir(parents=True, exist_ok=True)
        vp = video_path_from_manifest(data, mp)
        g = goal_override if goal_override is not None else str(data.get("goal", ""))
        started = datetime.now()
        out = _default_output_video(od, vp, output_video_name)
        return cls(
            run_id=rid,
            manifest_path=mp,
            output_dir=od,
            output_video=out,
            goal=g,
            started_at=started,
            video_path=vp,
        )

    @classmethod
    def fork(
        cls,
        manifest_path: Path,
        new_output_dir: Path,
        output_video_name: str | None = None,
    ) -> PipelineRun:
        """分叉：拷贝清单到新目录并分配新 run_id。"""
        src = manifest_path.resolve()
        data = load_manifest(src)
        new_od = Path(new_output_dir).resolve()
        new_od.mkdir(parents=True, exist_ok=True)
        new_mp = new_od / MANIFEST_FILENAME
        new_rid = str(ULID())
        data["run_id"] = new_rid
        save_manifest(new_mp, data, atomic=True)
        return cls.from_manifest(
            new_mp,
            goal_override=None,
            output_dir=new_od,
            output_video_name=output_video_name,
        )
