from dataclasses import dataclass, field
from fractions import Fraction
from typing import cast

import numpy as np
from av import AudioStream, Packet, VideoStream
from av import open as av_open
from av import time_base as AV_TIME_BASE
from av.container.input import InputContainer
from av.stream import Stream

from autosmartcut.backends.smartcut_core.nal_tools import (
    get_h264_nal_unit_type,
    get_h265_nal_unit_type,
    is_leading_picture_nal_type,
    is_rasl_nal_type,
    is_safe_h264_keyframe_nal,
    is_safe_h265_keyframe_nal,
)


def ts_to_time(ts: float) -> Fraction:
    return Fraction(round(ts * 1000), 1000)


def _multiply_array_by_fraction(args: tuple[np.ndarray, Fraction]) -> np.ndarray:
    arr, time_base = args
    return arr * time_base


@dataclass
class AudioTrack:
    media_container: "MediaContainer"
    av_stream: AudioStream
    path: str
    index: int

    packets: list[Packet] = field(default_factory=lambda: [])
    frame_times_pts: np.ndarray = field(default_factory=lambda: np.empty(()))
    frame_times: np.ndarray = field(default_factory=lambda: np.empty(()))


class MediaContainer:
    av_container: InputContainer
    video_stream: VideoStream | None
    path: str

    video_frame_times_pts: np.ndarray
    video_frame_times: np.ndarray
    video_keyframe_indices: list[int]
    gop_start_times_pts_s: list[int]

    gop_start_times_dts: list[int]
    gop_end_times_dts: list[int]
    gop_start_nal_types: list[int | None]
    gop_leading_end_dts: list[int | None]
    gop_has_rasl: list[bool]

    audio_tracks: list[AudioTrack]
    subtitle_tracks: list

    duration: Fraction
    start_time: Fraction

    def __init__(self, path: str) -> None:
        self.path = path
        frame_pts = []
        self.video_keyframe_indices = []
        self.av_container = av_container = av_open(path, "r", metadata_errors="ignore")
        self.chat_url = None
        self.chat_history = None
        self.chat_visualize = True
        self.start_time = Fraction(av_container.start_time, AV_TIME_BASE) if av_container.start_time is not None else Fraction(0)
        manual_duration_calc = av_container.duration is None
        self.duration = Fraction(av_container.duration, AV_TIME_BASE) if av_container.duration is not None else Fraction(0)

        is_h264 = False
        is_h265 = False
        streams: list[Stream]
        if len(av_container.streams.video) == 0:
            self.video_stream = None
            streams = [*av_container.streams.audio]
        else:
            self.video_stream = av_container.streams.video[0]
            self.video_stream.thread_type = "FRAME"
            streams = [self.video_stream, *av_container.streams.audio]
            if self.video_stream.codec_context.name == "hevc":
                is_h265 = True
            if self.video_stream.codec_context.name == "h264":
                is_h264 = True

        self.audio_tracks = []
        stream_index_to_audio_track = {}
        for i, audio_stream in enumerate(av_container.streams.audio):
            if audio_stream.time_base is None:
                continue
            audio_stream.codec_context.thread_type = "FRAME"
            track = AudioTrack(self, audio_stream, path, i)
            self.audio_tracks.append(track)
            stream_index_to_audio_track[audio_stream.index] = track

        self.subtitle_tracks = []
        stream_index_to_subtitle_track = {}
        for i, s in enumerate(av_container.streams.subtitles):
            streams.append(s)
            stream_index_to_subtitle_track[s.index] = i
            self.subtitle_tracks.append([])

        first_keyframe = True
        max_end_pts_by_stream: dict[int, int] = {}
        self.gop_start_times_dts = []
        self.gop_end_times_dts = []
        self.gop_start_nal_types = []
        self.gop_leading_end_dts = []
        self.gop_has_rasl = []
        last_seen_video_dts = None
        tracking_leading_in_cra = False
        current_gop_has_leading = False
        current_gop_has_rasl = False

        for packet in av_container.demux(streams):
            if packet.pts is None:
                continue

            if manual_duration_calc and (packet.pts is not None and packet.duration is not None):
                stream_idx = packet.stream_index
                end_pts = packet.pts + packet.duration
                if stream_idx not in max_end_pts_by_stream or end_pts > max_end_pts_by_stream[stream_idx]:
                    max_end_pts_by_stream[stream_idx] = end_pts

            if packet.stream.type == "video" and self.video_stream:
                if packet.is_keyframe:
                    nal_type = None
                    if is_h265:
                        nal_type = get_h265_nal_unit_type(bytes(packet))
                    elif is_h264:
                        nal_type = get_h264_nal_unit_type(bytes(packet))
                    is_safe_keyframe = True
                    if first_keyframe:
                        first_keyframe = False
                    elif is_h265:
                        is_safe_keyframe = is_safe_h265_keyframe_nal(nal_type)
                    elif is_h264:
                        is_safe_keyframe = is_safe_h264_keyframe_nal(nal_type)
                    if is_safe_keyframe:
                        if tracking_leading_in_cra:
                            self.gop_leading_end_dts.append(None if not current_gop_has_leading else last_seen_video_dts)
                            self.gop_has_rasl.append(current_gop_has_rasl)
                        self.video_keyframe_indices.append(len(frame_pts))
                        dts = packet.dts if packet.dts is not None else -100_000_000
                        self.gop_start_times_dts.append(dts)
                        self.gop_start_nal_types.append(nal_type)
                        if last_seen_video_dts is not None:
                            self.gop_end_times_dts.append(last_seen_video_dts)
                        if is_h265 and nal_type == 21:
                            tracking_leading_in_cra = True
                            current_gop_has_leading = False
                            current_gop_has_rasl = False
                        else:
                            tracking_leading_in_cra = False
                            current_gop_has_leading = False
                            current_gop_has_rasl = False
                            self.gop_leading_end_dts.append(None)
                            self.gop_has_rasl.append(False)
                elif tracking_leading_in_cra and is_h265:
                    packet_nal_type = get_h265_nal_unit_type(bytes(packet))
                    if is_leading_picture_nal_type(packet_nal_type):
                        current_gop_has_leading = True
                        if is_rasl_nal_type(packet_nal_type):
                            current_gop_has_rasl = True
                    else:
                        if current_gop_has_leading:
                            dts = packet.dts if packet.dts is not None else -100_000_000
                            self.gop_leading_end_dts.append(dts)
                        else:
                            self.gop_leading_end_dts.append(None)
                        self.gop_has_rasl.append(current_gop_has_rasl)
                        tracking_leading_in_cra = False

                last_seen_video_dts = packet.dts if packet.dts is not None else packet.pts
                frame_pts.append(packet.pts)
            elif packet.stream.type == "audio":
                track = stream_index_to_audio_track[packet.stream_index]
                track.last_packet = packet
                track.packets.append(packet)
            elif packet.stream.type == "subtitle":
                self.subtitle_tracks[stream_index_to_subtitle_track[packet.stream_index]].append(packet)

        if manual_duration_calc and max_end_pts_by_stream:
            for stream_idx, max_pts in max_end_pts_by_stream.items():
                stream = av_container.streams[stream_idx]
                if stream.time_base is None:
                    continue
                stream_duration = Fraction(max_pts) * stream.time_base
                if stream_duration > self.duration:
                    self.duration = stream_duration

        if self.video_stream is not None:
            if tracking_leading_in_cra:
                self.gop_leading_end_dts.append(None if not current_gop_has_leading else last_seen_video_dts)
                self.gop_has_rasl.append(current_gop_has_rasl)
            if len(self.gop_end_times_dts) < len(self.gop_start_times_dts):
                fallback_dts = last_seen_video_dts if last_seen_video_dts is not None else -100_000_000
                self.gop_end_times_dts.append(fallback_dts)
            assert len(self.gop_start_times_dts) == len(self.gop_end_times_dts)
            frame_pts_sorted = np.sort(np.array(frame_pts))
            self.video_frame_times_pts = frame_pts_sorted

        for t in self.audio_tracks:
            frame_pts_array = np.array(list(map(lambda p: p.pts, t.packets)))
            t.frame_times_pts = frame_pts_array

        from concurrent.futures import ThreadPoolExecutor

        tasks: list[tuple[np.ndarray, Fraction]] = []
        if self.video_stream is not None and self.video_stream.time_base is not None:
            tasks.append((self.video_frame_times_pts, self.video_stream.time_base))
        for t in self.audio_tracks:
            if t.av_stream.time_base is not None:
                tasks.append((t.frame_times_pts, t.av_stream.time_base))

        if tasks:
            with ThreadPoolExecutor() as executor:
                results = list(executor.map(_multiply_array_by_fraction, tasks))
            result_idx = 0
            if self.video_stream is not None and self.video_stream.time_base is not None:
                self.video_frame_times = results[result_idx]
                self.gop_start_times_pts_s = list(self.video_frame_times[self.video_keyframe_indices])
                result_idx += 1
            for t in self.audio_tracks:
                if t.av_stream.time_base is not None:
                    t.frame_times = results[result_idx]
                    result_idx += 1

    def close(self) -> None:
        self.av_container.close()

    def get_next_frame_time(self, t: Fraction) -> Fraction:
        assert self.video_stream is not None
        t += self.start_time
        t_pts = round(t / cast(Fraction, self.video_stream.time_base))
        idx = np.searchsorted(self.video_frame_times_pts, t_pts)
        if idx == len(self.video_frame_times_pts):
            return self.duration
        if idx == 0:
            return self.video_frame_times[0] - self.start_time
        prev_val = self.video_frame_times[idx - 1]
        next_val = self.video_frame_times[idx]
        return prev_val - self.start_time if t - prev_val <= next_val - t else next_val - self.start_time

    def get_frame_time_at_or_before(self, t: Fraction) -> Fraction:
        t_absolute = t + self.start_time
        if self.video_stream is not None:
            frame_times = self.video_frame_times
            frame_times_pts = self.video_frame_times_pts
            time_base = cast(Fraction, self.video_stream.time_base)
        elif self.audio_tracks:
            track = self.audio_tracks[0]
            frame_times = track.frame_times
            frame_times_pts = track.frame_times_pts
            time_base = cast(Fraction, track.av_stream.time_base)
        else:
            return t
        t_pts = round(t_absolute / time_base)
        idx = int(np.searchsorted(frame_times_pts, t_pts, side="right")) - 1
        idx = max(0, idx)
        return frame_times[idx] - self.start_time

    def get_frame_time_at_or_after(self, t: Fraction) -> Fraction:
        t_absolute = t + self.start_time
        if self.video_stream is not None:
            frame_times = self.video_frame_times
            frame_times_pts = self.video_frame_times_pts
            time_base = cast(Fraction, self.video_stream.time_base)
        elif self.audio_tracks:
            track = self.audio_tracks[0]
            frame_times = track.frame_times
            frame_times_pts = track.frame_times_pts
            time_base = cast(Fraction, track.av_stream.time_base)
        else:
            return t
        t_pts = round(t_absolute / time_base)
        idx = int(np.searchsorted(frame_times_pts, t_pts, side="left"))
        if idx >= len(frame_times):
            return self.duration
        return frame_times[idx] - self.start_time

