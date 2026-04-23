"""Media utilities and enums for Smart Media Cutter.

This module contains all media-related enums, codec compatibility helpers,
and low-level media knowledge. All media format details should be centralized here.
"""

from enum import Enum


class VideoExportMode(Enum):
    """Video export modes."""

    SMARTCUT = 1  # Recode only around cutpoints (fast and accurate)
    KEYFRAMES = 2  # Cut on keyframes (inaccurate timing, lossless, very fast)
    RECODE = 3  # Recode the whole video (slow)


class VideoExportQuality(Enum):
    """Video quality presets."""

    LOW = 1  # "Low"
    NORMAL = 2  # "Normal"
    HIGH = 3  # "High"
    INDISTINGUISHABLE = 4  # "Almost indistinguishable (Large file size)"
    NEAR_LOSSLESS = 5  # "Near lossless (Huge file size)"
    LOSSLESS = 6  # "Lossless (Extremely large file size)"


class VideoCodec(Enum):
    """Video codecs with PyAV-compatible string values."""

    COPY = "copy"  # No re-encoding
    H264 = "h264"  # H.264 encoding
    HEVC = "hevc"  # H.265/HEVC encoding
    VP9 = "vp9"  # VP9 encoding
    AV1 = "av1"  # AV1 encoding


class AudioCodec(Enum):
    """Audio codecs with PyAV-compatible string values."""

    LIBOPUS = "libopus"  # Opus codec
    LIBVORBIS = "libvorbis"  # Vorbis codec
    AAC = "aac"  # AAC codec
    MP3 = "libmp3lame"  # MP3 codec (correct PyAV codec name)
    FLAC = "flac"  # FLAC lossless codec
    PCM_S16LE = "pcm_s16le"  # 16-bit PCM
    PCM_F32LE = "pcm_f32le"  # 32-bit float PCM
    PASSTHRU = "passthru"  # Pass through without re-encoding


class AudioChannels(Enum):
    """Audio channel configuration."""

    MONO = "mono"
    STEREO = "stereo"
    SURROUND_5_1 = "5.1"


def get_crf_for_quality(quality: VideoExportQuality) -> int:
    """Get CRF value for the selected quality preset."""
    crf_map = {
        VideoExportQuality.LOW: 23,
        VideoExportQuality.NORMAL: 18,
        VideoExportQuality.HIGH: 14,
        VideoExportQuality.INDISTINGUISHABLE: 8,
        VideoExportQuality.NEAR_LOSSLESS: 3,
        VideoExportQuality.LOSSLESS: 0,
    }
    return crf_map.get(quality, 18)


def get_compatible_codec_for_format(user_codec: AudioCodec, file_extension: str) -> str:
    """Get compatible audio codec for the given file format."""
    extension_codec_map = {
        "mp3": AudioCodec.MP3.value,
        "flac": AudioCodec.FLAC.value,
        "ogg": AudioCodec.LIBOPUS.value,
        "wav": AudioCodec.PCM_S16LE.value,
        "m4a": AudioCodec.AAC.value,
        "ipod": AudioCodec.AAC.value,
    }
    if file_extension.lower() in extension_codec_map:
        return extension_codec_map[file_extension.lower()]
    return user_codec.value


def get_audio_only_formats() -> list[str]:
    return ["mp3", "flac", "ogg", "wav", "m4a", "ipod"]


def is_audio_only_format(file_extension: str) -> bool:
    ext = file_extension.lower().lstrip(".")
    return ext in get_audio_only_formats()


def _normalize_video_codec_name(name: str) -> str:
    if not name:
        return ""
    n = name.strip().lower()
    if n == "h265":
        return "hevc"
    return n


def validate_video_container_compat(encoder_name: str, container_ext: str) -> list[str]:
    errors: list[str] = []
    enc = _normalize_video_codec_name(encoder_name)
    ext = container_ext.lower().lstrip(".")
    if enc == "h264" and ext == "ogg":
        errors.append("H.264 video codec is not supported in OGG containers")
    if enc == "hevc" and ext in ["mp3", "ogg"]:
        errors.append(f"H.265 video codec is not supported in {ext.upper()} containers")
    return errors


def validate_audio_track_limits_for_container(container_ext: str, total_audio_tracks: int) -> list[str]:
    errors: list[str] = []
    if total_audio_tracks <= 1:
        return errors
    ext = container_ext.lower().lstrip(".")
    single_track_formats = ["ogg", "mp3", "m4a", "flac", "wav"]
    if ext in single_track_formats:
        errors.append(f"{ext.upper()} format can only have 1 audio track, but {total_audio_tracks} were selected")
    return errors


def get_valid_audio_codecs_for_container(container_ext: str) -> list[AudioCodec]:
    ext = container_ext.lower().lstrip(".")
    audio_only_map = {
        "mp3": [AudioCodec.MP3],
        "flac": [AudioCodec.FLAC],
        "wav": [AudioCodec.PCM_S16LE, AudioCodec.PCM_F32LE],
        "ogg": [AudioCodec.LIBOPUS, AudioCodec.LIBVORBIS],
        "m4a": [AudioCodec.AAC],
        "ipod": [AudioCodec.AAC],
    }
    if ext in audio_only_map:
        return audio_only_map[ext]
    video_container_map = {
        "mp4": [AudioCodec.AAC, AudioCodec.MP3],
        "mov": [AudioCodec.AAC, AudioCodec.MP3],
        "mkv": [AudioCodec.AAC, AudioCodec.MP3, AudioCodec.LIBOPUS, AudioCodec.FLAC, AudioCodec.PCM_S16LE],
        "webm": [AudioCodec.LIBOPUS, AudioCodec.LIBVORBIS],
        "avi": [AudioCodec.MP3, AudioCodec.PCM_S16LE],
    }
    return video_container_map.get(ext, [AudioCodec.AAC, AudioCodec.MP3])


def get_valid_video_codecs_for_container(container_ext: str) -> list[VideoCodec]:
    ext = container_ext.lower().lstrip(".")
    container_map = {
        "mp4": [VideoCodec.H264, VideoCodec.HEVC, VideoCodec.AV1],
        "mov": [VideoCodec.H264, VideoCodec.HEVC],
        "mkv": [VideoCodec.H264, VideoCodec.HEVC, VideoCodec.VP9, VideoCodec.AV1],
        "webm": [VideoCodec.VP9, VideoCodec.AV1],
        "avi": [VideoCodec.H264],
    }
    return container_map.get(ext, [VideoCodec.H264, VideoCodec.HEVC])


def get_default_audio_codec_for_container(container_ext: str) -> AudioCodec:
    ext = container_ext.lower().lstrip(".")
    audio_only_defaults = {
        "mp3": AudioCodec.MP3,
        "flac": AudioCodec.FLAC,
        "wav": AudioCodec.PCM_S16LE,
        "ogg": AudioCodec.LIBOPUS,
        "m4a": AudioCodec.AAC,
        "ipod": AudioCodec.AAC,
    }
    if ext in audio_only_defaults:
        return audio_only_defaults[ext]
    video_defaults = {
        "mp4": AudioCodec.AAC,
        "mov": AudioCodec.AAC,
        "mkv": AudioCodec.AAC,
        "webm": AudioCodec.LIBOPUS,
        "avi": AudioCodec.MP3,
    }
    return video_defaults.get(ext, AudioCodec.AAC)


def get_default_video_codec_for_container(container_ext: str) -> VideoCodec:
    ext = container_ext.lower().lstrip(".")
    container_defaults = {
        "mp4": VideoCodec.H264,
        "mov": VideoCodec.H264,
        "mkv": VideoCodec.H264,
        "webm": VideoCodec.VP9,
        "avi": VideoCodec.H264,
    }
    return container_defaults.get(ext, VideoCodec.H264)

