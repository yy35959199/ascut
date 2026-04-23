from fractions import Fraction
from typing import cast

import numpy as np
from av import AudioStream
from av.container.output import OutputContainer
from av.packet import Packet
from av.stream import Disposition, Stream

from autosmartcut.backends.smartcut_core.media_container import MediaContainer
from autosmartcut.backends.smartcut_core.misc_data import CutSegment
from autosmartcut.backends.smartcut_core.video_cutter import copy_packet


def create_audio_output_stream(
    media_container: MediaContainer,
    output_av_container: OutputContainer,
    track_index: int,
) -> AudioStream:
    track = media_container.audio_tracks[track_index]
    out_stream = output_av_container.add_stream_from_template(
        track.av_stream,
        options={"x265-params": "log_level=error"},
    )
    out_stream.metadata.update(track.av_stream.metadata)
    out_stream.disposition = cast(Disposition, track.av_stream.disposition.value)
    return out_stream


class PassthruAudioCutter:
    def __init__(
        self,
        media_container: MediaContainer,
        out_stream: AudioStream,
        track_index: int,
        initial_position: Fraction = Fraction(0),
        initial_prev_dts: int = -100_000,
        initial_prev_pts: int = -100_000,
    ) -> None:
        self.track = media_container.audio_tracks[track_index]
        self.out_stream = out_stream
        self.segment_start_in_output = initial_position
        self.prev_dts = initial_prev_dts
        self.prev_pts = initial_prev_pts

    def segment(self, cut_segment: CutSegment) -> list[Packet]:
        in_tb = cast(Fraction, self.track.av_stream.time_base)
        if cut_segment.start_time <= 0:
            start = 0
        else:
            start_pts = round(cut_segment.start_time / in_tb)
            start = np.searchsorted(self.track.frame_times_pts, start_pts)
        end_pts = round(cut_segment.end_time / in_tb)
        end = np.searchsorted(self.track.frame_times_pts, end_pts)
        in_packets = self.track.packets[start:end]
        packets = []
        for p in in_packets:
            if p.dts is None or p.pts is None:
                continue
            packet = copy_packet(p)
            packet.stream = self.out_stream
            packet.pts = int(p.pts + (self.segment_start_in_output - cut_segment.start_time) / in_tb)
            packet.dts = int(p.dts + (self.segment_start_in_output - cut_segment.start_time) / in_tb)
            if packet.pts <= self.prev_pts:
                packet.pts = self.prev_pts + 1
            if packet.dts <= self.prev_dts:
                packet.dts = self.prev_dts + 1
            self.prev_pts = packet.pts
            self.prev_dts = packet.dts
            packets.append(packet)

        self.segment_start_in_output += cut_segment.end_time - cut_segment.start_time
        return packets

    def finish(self) -> list[Packet]:
        return []


def create_subtitle_output_stream(
    media_container: MediaContainer,
    output_av_container: OutputContainer,
    track_index: int,
) -> Stream:
    in_stream = media_container.av_container.streams.subtitles[track_index]
    out_stream = output_av_container.add_stream_from_template(in_stream)
    out_stream.metadata.update(in_stream.metadata)
    out_stream.disposition = cast(Disposition, in_stream.disposition.value)
    return out_stream


class SubtitleCutter:
    def __init__(
        self,
        media_container: MediaContainer,
        out_stream: Stream,
        track_index: int,
        initial_position: Fraction = Fraction(0),
        initial_prev_pts: int = -100_000,
    ) -> None:
        self.track_i = track_index
        self.packets = media_container.subtitle_tracks[track_index]
        self.in_stream = media_container.av_container.streams.subtitles[track_index]
        self.out_stream = out_stream
        self.segment_start_in_output = initial_position
        self.prev_pts = initial_prev_pts
        self.current_packet_i = 0

    def segment(self, cut_segment: CutSegment) -> list[Packet]:
        in_tb = cast(Fraction, self.in_stream.time_base)
        segment_start_pts = int(cut_segment.start_time / in_tb)
        segment_end_pts = int(cut_segment.end_time / in_tb)
        out_packets = []
        while self.current_packet_i < len(self.packets):
            p = self.packets[self.current_packet_i]
            if p.pts < segment_start_pts:
                self.current_packet_i += 1
            elif p.pts >= segment_start_pts and p.pts < segment_end_pts:
                out_packets.append(p)
                self.current_packet_i += 1
            else:
                break

        for packet in out_packets:
            packet.stream = self.out_stream
            packet.pts = int(packet.pts - segment_start_pts + self.segment_start_in_output / in_tb)
            if packet.pts < self.prev_pts:
                packet.pts = self.prev_pts + 1
            packet.dts = packet.pts
            self.prev_pts = packet.pts
            self.prev_dts = packet.dts

        self.segment_start_in_output += cut_segment.end_time - cut_segment.start_time
        return out_packets

    def finish(self) -> list[Packet]:
        return []

