from __future__ import annotations

from dataclasses import dataclass

from autosmartcut.l3_models import ResolvedTask


@dataclass(frozen=True)
class EncodeMissResult:
    fallback_required: bool
    fallback_count: int


def encode_misses_sync(resolved: list[ResolvedTask]) -> EncodeMissResult:
    """当前版本：只统计 miss，回退到统一 full render 路径。

    说明：
    - 这里不吞掉 miss，而是显式返回 fallback_required，便于后续并行预编码接入。
    - 未来接入真实按任务补编码时，只需替换本函数实现，不影响 orchestrator。
    """

    miss_count = sum(1 for item in resolved if not item.hit)
    return EncodeMissResult(
        fallback_required=miss_count > 0,
        fallback_count=miss_count,
    )

