from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING

from autosmartcut.backends.smartcut_core.media_utils import VideoExportMode, VideoExportQuality
from autosmartcut.l3_errors import L3EncodeError

if TYPE_CHECKING:  # pragma: no cover
    from autosmartcut.backends.smartcut_core.media_container import MediaContainer


class SmartcutBackendError(L3EncodeError):
    """Raised when smartcut backend fails."""


@dataclass(frozen=True)
class SmartcutRenderOptions:
    video_mode: VideoExportMode = VideoExportMode.SMARTCUT
    video_quality: VideoExportQuality = VideoExportQuality.LOW
    audio_passthru: bool = True


def probe_duration_seconds(video_path: Path) -> float:
    from autosmartcut.backends.smartcut_core.media_container import MediaContainer

    media = MediaContainer(str(video_path))
    try:
        return float(media.duration)
    finally:
        media.close()


def render_segments(
    *,
    source_video: Path,
    output_video: Path,
    positive_segments: list[tuple[Fraction, Fraction]],
    options: SmartcutRenderOptions | None = None,
) -> None:
    from autosmartcut.backends.smartcut_core.media_container import MediaContainer
    from autosmartcut.backends.smartcut_core.misc_data import AudioExportInfo, AudioExportSettings
    from autosmartcut.backends.smartcut_core.smart_cut import smart_cut
    from autosmartcut.backends.smartcut_core.video_cutter import VideoSettings

    opts = options or SmartcutRenderOptions()
    media = MediaContainer(str(source_video))
    try:
        if not media.audio_tracks:
            raise SmartcutBackendError("输入文件没有音轨")
        audio_info = None
        if opts.audio_passthru:
            audio_info = AudioExportInfo(
                output_tracks=[AudioExportSettings(codec="passthru") for _ in media.audio_tracks]
            )
        err = smart_cut(
            media_container=media,
            positive_segments=positive_segments,
            out_path=str(output_video),
            audio_export_info=audio_info,
            video_settings=VideoSettings(opts.video_mode, opts.video_quality),
        )
        if err is not None:
            raise SmartcutBackendError(f"smart_cut 失败: {err}")
    except SmartcutBackendError:
        raise
    except Exception as exc:  # pragma: no cover - backend third-party failures
        raise SmartcutBackendError(f"smartcut 后端异常: {exc}") from exc
    finally:
        media.close()

