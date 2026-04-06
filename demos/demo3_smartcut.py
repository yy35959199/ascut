# Demo 3：执行层验证 — smartcut 库集成
from __future__ import annotations

"""
模式 dense：不依赖 layer1，用合成密集 EDL 压测 smartcut。
模式 json：读取 layer1_annotations.json + layer2_output_mock.json，走 execution.keep_mask 管线。

示例（在仓库 ascut 目录下，且已 conda activate ascut）：
  python demos/demo3_smartcut.py dense --input samples/alxe_01.mp4
  python demos/demo3_smartcut.py json --layer1 outputs/layer1_annotations.json --mask outputs/layer2_output_mock.json
"""

import argparse
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from smartcut.media_container import MediaContainer
from smartcut.misc_data import AudioExportInfo, AudioExportSettings
from smartcut.smart_cut import smart_cut

from autosmartcut.execution import positive_segments_from_mask_files


@dataclass
class EditDecision:
    t_start: float
    t_end: float
    action: str


def build_dense_edl(total_sec: float, target_keeps: int = 52) -> list[EditDecision]:
    edl: list[EditDecision] = []
    t = 0.0
    keep_dur = 2.5
    cut_dur = 0.8
    keeps = 0
    while t < total_sec - 0.05 and keeps < target_keeps:
        end = min(t + keep_dur, total_sec)
        if end - t > 0.05:
            edl.append(EditDecision(t_start=t, t_end=end, action="keep"))
            keeps += 1
        t = end
        if t >= total_sec - 0.05:
            break
        t = min(t + cut_dur, total_sec)
    return edl


def edl_to_positive_segments(edl: list[EditDecision]) -> list[tuple[Fraction, Fraction]]:
    return [
        (
            Fraction(e.t_start).limit_denominator(1_000_000),
            Fraction(e.t_end).limit_denominator(1_000_000),
        )
        for e in edl
        if e.action == "keep"
    ]


def run_dense(args: argparse.Namespace) -> None:
    inp = Path(args.input)
    if not inp.is_file():
        raise SystemExit(f"找不到输入: {inp}")
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    media = MediaContainer(str(inp))
    try:
        if not media.audio_tracks:
            raise SystemExit("输入文件没有音轨，本 Demo 要求至少一条音轨")
        total_sec = float(media.duration)
        if total_sec <= 0:
            raise SystemExit("无法取得有效时长")

        edl = build_dense_edl(total_sec, target_keeps=args.target_keeps)
        positive = edl_to_positive_segments(edl)
        if not positive:
            raise SystemExit("未生成任何 keep 区间")

        print(f"时长 {total_sec:.2f}s，keep 段数 {len(positive)}，输出 → {out}")

        audio_info = AudioExportInfo(
            output_tracks=[AudioExportSettings(codec="passthru") for _ in media.audio_tracks]
        )
        err = smart_cut(
            media_container=media,
            positive_segments=positive,
            out_path=str(out),
            audio_export_info=audio_info,
        )
        if err is not None:
            raise SystemExit(f"smart_cut 失败: {err}")
    finally:
        media.close()
    print("完成。")


def run_json(args: argparse.Namespace) -> None:
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    positive, video, duration = positive_segments_from_mask_files(
        Path(args.layer1),
        Path(args.mask),
        pre_pad=args.pre_pad,
        post_pad=args.post_pad,
        min_duration=args.min_duration,
    )
    if not positive:
        raise SystemExit("keep_mask 解析后无保留区间（请检查 cut 比例或源标注）")

    print(f"源视频 {video}，时长 {duration:.2f}s，keep 段数 {len(positive)}，输出 → {out}")

    media = MediaContainer(str(video))
    try:
        if not media.audio_tracks:
            raise SystemExit("输入文件没有音轨")
        audio_info = AudioExportInfo(
            output_tracks=[AudioExportSettings(codec="passthru") for _ in media.audio_tracks]
        )
        err = smart_cut(
            media_container=media,
            positive_segments=positive,
            out_path=str(out),
            audio_export_info=audio_info,
        )
        if err is not None:
            raise SystemExit(f"smart_cut 失败: {err}")
    finally:
        media.close()
    print("完成。")


def main() -> None:
    parser = argparse.ArgumentParser(description="Demo 3：smartcut 执行层")
    sub = parser.add_subparsers(dest="mode", required=True)

    p_dense = sub.add_parser("dense", help="合成密集 EDL（不读 layer1）")
    p_dense.add_argument("--input", required=True, help="输入视频")
    p_dense.add_argument(
        "--output",
        default=str(_ROOT / "outputs" / "demo3_cut.mp4"),
        help="输出视频路径",
    )
    p_dense.add_argument("--target-keeps", type=int, default=52, help="目标 keep 段数上限")
    p_dense.set_defaults(func=run_dense)

    p_json = sub.add_parser("json", help="layer1 + keep_mask JSON")
    p_json.add_argument("--layer1", required=True, type=Path, help="layer1_annotations.json")
    p_json.add_argument("--mask", required=True, type=Path, help="layer2_output_mock.json")
    p_json.add_argument(
        "--output",
        default=str(_ROOT / "outputs" / "demo3_from_mask.mp4"),
        help="输出视频路径",
    )
    p_json.add_argument("--pre-pad", type=float, default=0.15, help="区间前 padding（秒）")
    p_json.add_argument("--post-pad", type=float, default=0.25, help="区间后 padding（秒）")
    p_json.add_argument("--min-duration", type=float, default=1.0, help="过短区间合并阈值（秒）")
    p_json.set_defaults(func=run_json)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
