from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

AssetState = Literal["ready", "stale", "invalid", "failed"]
MissReason = Literal[
    "not_found",
    "signature_mismatch",
    "asset_missing",
    "asset_invalid",
]


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class FinalSegment:
    idx: int
    start: Fraction
    end: Fraction

    def to_json(self) -> dict[str, Any]:
        return {
            "idx": self.idx,
            "start_sec": float(self.start),
            "end_sec": float(self.end),
        }


@dataclass(frozen=True)
class L3FinalPlan:
    run_id: str
    source_video: Path
    output_video: Path
    segments: list[FinalSegment]
    params_signature: str
    created_at: str = field(default_factory=utc_now_iso)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "source": {"path": str(self.source_video)},
            "output": {"path": str(self.output_video)},
            "timeline": {
                "segments": [s.to_json() for s in self.segments],
                "segment_count": len(self.segments),
            },
            "signatures": {"plan_signature": self.params_signature},
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class SeamTask:
    task_id: str
    segment_idx: int
    boundary: Literal["in", "out"]
    target_cut: Fraction
    cache_key: str
    priority: int = 100

    def to_json(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "segment_idx": self.segment_idx,
            "boundary": self.boundary,
            "target_cut_sec": float(self.target_cut),
            "cache_key": self.cache_key,
            "priority": self.priority,
        }


@dataclass
class ResolvedTask:
    task: SeamTask
    hit: bool
    asset_path: Path | None = None
    asset_state: AssetState | None = None
    miss_reason: MissReason | None = None

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["asset_path"] = str(self.asset_path) if self.asset_path else None
        data["task"] = self.task.to_json()
        return data


@dataclass(frozen=True)
class L3RunMetrics:
    resolve_assets_ms: int
    encode_miss_ms: int
    assemble_mux_ms: int
    total_l3_ms: int
    task_total: int
    task_hit: int
    task_miss: int
    fallback_count: int
    status: Literal["success", "failed"]
    output_path: str
    finished_at: str = field(default_factory=utc_now_iso)
    sentence_tile_fast_path: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "timing_ms": {
                "resolve_assets": self.resolve_assets_ms,
                "encode_misses": self.encode_miss_ms,
                "assemble_mux": self.assemble_mux_ms,
                "total_l3": self.total_l3_ms,
            },
            "counters": {
                "task_total": self.task_total,
                "task_hit": self.task_hit,
                "task_miss": self.task_miss,
                "fallback_count": self.fallback_count,
            },
            "ratios": {
                "cache_hit_ratio": (self.task_hit / self.task_total) if self.task_total else 0.0,
                "sentence_tile_fast_path": self.sentence_tile_fast_path,
            },
            "result": {
                "status": self.status,
                "output_path": self.output_path,
            },
            "finished_at": self.finished_at,
        }

