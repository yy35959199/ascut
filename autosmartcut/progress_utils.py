"""progress_utils.py — 进度估算工具。

供 L1A 渐进式 ASR 和消费层（CLI/TUI）共用：
- SpeedEstimator：基于已完成块的速度估算，支持块内插值
- format_duration：将秒数格式化为人类可读字符串
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# SpeedEstimator
# ---------------------------------------------------------------------------

@dataclass
class _ChunkRecord:
    chunk_id: int
    audio_sec: float
    elapsed_sec: float


class SpeedEstimator:
    """基于已完成 ASR 块的速度估算器。

    设计原则：
    - 块 0 含 GPU warmup，速度偏慢，不参与稳态速度计算
    - 从块 1 开始累积稳态速度（audio_sec / elapsed_sec）
    - speed 属性在稳态数据不足时返回 None（消费层显示"—"）
    - interpolate() 用于块内伪进度，带 ease-out 避免超过 95%
    """

    def __init__(self) -> None:
        self._records: list[_ChunkRecord] = []

    def record(self, chunk_id: int, audio_sec: float, elapsed_sec: float) -> None:
        """记录一个已完成块的音频时长和实际耗时。"""
        if audio_sec <= 0 or elapsed_sec <= 0:
            return
        self._records.append(_ChunkRecord(chunk_id, audio_sec, elapsed_sec))

    @property
    def speed(self) -> float | None:
        """稳态速度（秒音频 / 秒实时）。

        排除块 0（GPU warmup），使用块 1+ 的累积平均。
        数据不足时返回 None。
        """
        stable = [r for r in self._records if r.chunk_id >= 1]
        if not stable:
            return None
        total_audio = sum(r.audio_sec for r in stable)
        total_elapsed = sum(r.elapsed_sec for r in stable)
        if total_elapsed <= 0:
            return None
        return total_audio / total_elapsed

    def estimate_remaining(self, remaining_audio_sec: float) -> float | None:
        """估算剩余挂钟时间（秒）。

        Args:
            remaining_audio_sec: 剩余音频时长（秒）

        Returns:
            预计剩余秒数，或 None（速度数据不足）
        """
        s = self.speed
        if s is None or s <= 0:
            return None
        if remaining_audio_sec <= 0:
            return 0.0
        return remaining_audio_sec / s

    def interpolate(self, chunk_audio_sec: float, wall_elapsed: float) -> float:
        """估算当前块内的完成比例（0.0 ~ 0.95，带 ease-out）。

        用于伪进度条：当 GPU 正在推理时，根据已消耗时间和预测总时间
        线性插值出一个进度值，但永远不超过 0.95，避免"卡在 100%"的感觉。

        Args:
            chunk_audio_sec: 当前块的音频时长（秒）
            wall_elapsed: 当前块已消耗的挂钟时间（秒）

        Returns:
            完成比例 [0.0, 0.95]
        """
        s = self.speed
        if s is None or s <= 0 or chunk_audio_sec <= 0:
            return 0.0
        predicted_total = chunk_audio_sec / s
        if predicted_total <= 0:
            return 0.0
        raw_ratio = wall_elapsed / predicted_total
        # ease-out: 快速接近但永远不到 1.0，上限 0.95
        eased = 1.0 - math.exp(-raw_ratio * 2.0)
        return min(eased * 0.95, 0.95)


# ---------------------------------------------------------------------------
# format_duration
# ---------------------------------------------------------------------------

def format_duration(seconds: float) -> str:
    """将秒数格式化为紧凑的人类可读字符串。

    Examples:
        format_duration(0.0)    → "0s"
        format_duration(45.0)   → "45s"
        format_duration(150.0)  → "2m30s"
        format_duration(3661.0) → "1h1m1s"
        format_duration(-5.0)   → "0s"  (负数视为 0)
    """
    if not math.isfinite(seconds) or seconds < 0:
        return "0s"
    total = int(seconds)
    if total == 0:
        return "0s"
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return "".join(parts)
