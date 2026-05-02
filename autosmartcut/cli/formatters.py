"""formatters.py — 事件 payload 与人类可读文本（CLI / TUI / Shell 共用）。

不依赖任何 UI 框架；供 CLIAdapter、TUI widgets、intelligence_2d_shell 等使用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autosmartcut.nodes.l2.intelligence_2d_core import DisplayData


# ---------------------------------------------------------------------------
# 视图模型（View Model）：payload 解析与渲染之间的稳定接口
# ---------------------------------------------------------------------------

@dataclass
class L1aChunkProgressState:
    """L1A 块内进度的视图模型（对应 asr_intra_chunk_progress 事件）。"""
    chunk_id: int
    total_chunks: int
    pct: float
    remain_str: str
    speed_str: str


@dataclass
class L1aChunkDoneState:
    """L1A 块完成的视图模型（对应 asr_chunk_done 事件）。"""
    chunk_id: int
    total_chunks: int
    text: str


def parse_l1a_intra_chunk_progress(payload: dict) -> L1aChunkProgressState:
    """从 asr_intra_chunk_progress payload 提取视图模型。"""
    from autosmartcut.cli.progress_utils import format_duration
    remaining = payload.get("remaining_sec")
    speed = payload.get("estimated_speed")
    return L1aChunkProgressState(
        chunk_id=int(payload.get("chunk_id", 0)),
        total_chunks=int(payload.get("total_chunks", 1)),
        pct=float(payload.get("pct", 0.0)),
        remain_str=format_duration(remaining) if remaining is not None else "—",
        speed_str=f"{speed:.2f}x" if speed else "",
    )


def parse_l1a_chunk_done(payload: dict) -> L1aChunkDoneState:
    """从 asr_chunk_done payload 提取视图模型。"""
    return L1aChunkDoneState(
        chunk_id=int(payload.get("chunk_id", 0)),
        total_chunks=int(payload.get("total_chunks", 1)),
        text=str(payload.get("text_full", payload.get("text_preview", ""))).strip(),
    )


# ---------------------------------------------------------------------------
# 格式化函数
# ---------------------------------------------------------------------------

def format_review_summary(review_report: dict) -> str:
    """格式化 2c 审核报告为文本。"""
    if not review_report:
        return "（无审核报告）"

    lines: list[str] = []
    verdict = review_report.get("verdict", "unknown")
    lines.append(f"审核结论: {verdict}")

    checklist = review_report.get("checklist", [])
    judgments = review_report.get("judgments", [])

    judgment_map: dict[int, dict] = {}
    for j in judgments:
        idx = j.get("checklist_index", j.get("index", -1))
        judgment_map[idx] = j

    for i, item in enumerate(checklist):
        label = item.get("label", item.get("text", f"项目 {i}"))
        priority = item.get("priority", "")
        j = judgment_map.get(i, {})
        covered = j.get("covered", j.get("pass", False))
        mark = "✓" if covered else "✗"
        priority_tag = f"[{priority}]" if priority else ""
        lines.append(f"  {mark} {priority_tag} {label}")

    return "\n".join(lines)


def format_decision_list(
    display_data: "DisplayData",
    *,
    use_markup: bool = False,
) -> str:
    """格式化决策列表为文本，按 outline_blocks 分组。"""
    tokens = display_data.tokens
    mask = display_data.effective_mask
    comp = display_data.comprehension
    outline_blocks = comp.get("outline_blocks", [])

    lines: list[str] = []

    if outline_blocks:
        for block in outline_blocks:
            block_label = block.get("label", block.get("title", ""))
            start = block.get("start", 0)
            end = block.get("end", len(tokens))
            lines.append(f"\n── {block_label} [{start}-{end}] ──")
            for i in range(start, min(end + 1, len(tokens))):
                keep = mask[i]["keep"] if i < len(mask) else True
                if keep:
                    status = "[yellow][保留][/yellow]" if use_markup else "[保留]"
                else:
                    status = "[删除]"
                text = tokens[i].get("text", "")
                preview = (text[:60] + "…") if len(text) > 63 else text
                if not preview:
                    preview = "(空)"
                if use_markup and keep:
                    lines.append(f"  {status}[{i}] [rgb(128,255,181)]{preview}[/rgb(128,255,181)]")
                else:
                    lines.append(f"  {status}[{i}] {preview}")
    else:
        for i, tok in enumerate(tokens):
            keep = mask[i]["keep"] if i < len(mask) else True
            if keep:
                status = "[yellow][保留][/yellow]" if use_markup else "[保留]"
            else:
                status = "[删除]"
            text = tok.get("text", "")
            preview = (text[:60] + "…") if len(text) > 63 else text
            if not preview:
                preview = "(空)"
            if use_markup and keep:
                lines.append(f"  {status}[{i}] [rgb(128,255,181)]{preview}[/rgb(128,255,181)]")
            else:
                lines.append(f"  {status}[{i}] {preview}")

    return "\n".join(lines)


def format_stats(stats: dict) -> str:
    """格式化统计栏。"""
    return (
        f"保留: {stats.get('keep_count', 0)} | "
        f"删除: {stats.get('cut_count', 0)} | "
        f"总数: {stats.get('total', 0)} | "
        f"人工修改: {stats.get('override_count', 0)}"
    )


def format_progress(node_id: str, phase: str, payload: dict) -> str:
    """将结构化 ProgressEvent 格式化为人类可读文本。

    未知 node_id/phase 组合降级为 "[node_id] phase"。
    """
    p = payload
    match (node_id, phase):
        # ── L1（合并 ASR + 对齐）──────────────────────────────────────────
        case ("l1_perception", "transcode_start"):
            return "  音频转码中..."
        case ("l1_perception", "transcode_done"):
            return f"  音频转码完成 ({p.get('elapsed_sec', 0):.1f}s)"
        case ("l1_perception", "vad_start"):
            return "  语音段分析中..."
        case ("l1_perception", "vad_done"):
            return (
                f"  语音段分析完成 ({p.get('elapsed_sec', 0):.1f}s)，"
                f"检测到 {p.get('speech_segment_count', 0)} 个语音段"
            )
        case ("l1_perception", "plan_done"):
            dur = p.get("total_audio_sec", 0)
            n = p.get("total_chunks", 0)
            return f"  音频 {dur:.0f}s 分为 {n} 块，开始识别与对齐..."
        case ("l1_perception", "asr_chunk_start"):
            chunk_id = p.get("chunk_id", 0)
            total = p.get("total_chunks", 1)
            return "  正在计算剩余时间..." if chunk_id == 0 else f"  ASR [{chunk_id + 1}/{total}] 推理中..."
        case ("l1_perception", "asr_chunk_done"):
            chunk_id = p.get("chunk_id", 0)
            total = p.get("total_chunks", 1)
            speed = p.get("estimated_speed")
            speed_str = f"{speed:.2f}x" if speed else "—"
            preview = p.get("text_preview", "")
            al = p.get("align_elapsed_sec")
            al_str = f" align={al:.1f}s" if isinstance(al, (int, float)) and al > 0 else ""
            return (
                f"  ASR [{chunk_id + 1}/{total}] 完成 speed={speed_str}{al_str} | {preview[:50]}"
            )
        case ("l1_perception", "asr_computing_speed"):
            return "  正在计算剩余时间..."
        case ("l1_perception", "asr_intra_chunk_progress"):
            s = parse_l1a_intra_chunk_progress(p)
            bar_width = 20
            filled = int(s.pct / 100 * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            speed = f" {s.speed_str}" if s.speed_str else ""
            return f"  [{s.chunk_id+1}/{s.total_chunks}] {bar} {s.pct:.0f}%  剩余 {s.remain_str}{speed}"
        case ("l1_perception", "postprocess_done"):
            return (
                f"  L1 后处理完成：{p.get('sentence_count', 0)} 句，"
                f"{p.get('raw_text_length', 0)} 字符"
            )
        # ── L2A ──────────────────────────────────────────────────────────
        case ("l2a_comprehension", "r1_start"):
            return "  R1 粗理解中..."
        case ("l2a_comprehension", "r1_done"):
            return f"  R1 完成 ({p.get('elapsed_sec', 0):.1f}s)"
        case ("l2a_comprehension", "r2_start"):
            return "  R2 精化主旨与分块中..."
        case ("l2a_comprehension", "r2_done"):
            purpose = p.get("purpose", "")
            preview = (purpose[:60] + "…") if len(purpose) > 63 else purpose
            return (
                f"  L2A 完成 ({p.get('elapsed_sec', 0):.1f}s)："
                f"{p.get('block_count', 0)} 块，{p.get('correction_count', 0)} 处纠错 | {preview}"
            )
        # ── L2B ──────────────────────────────────────────────────────────
        case ("l2b_decision", "decision_start"):
            mode = p.get("mode", "")
            round_ = p.get("review_round", 0)
            rerun = "（2c 修正重跑）" if p.get("is_rerun") else ""
            return f"  决策中（轮次 {round_}，模式 {mode}）{rerun}..."
        case ("l2b_decision", "block_done"):
            return f"  决策块 {p.get('block_index', 0) + 1}/{p.get('total_blocks', 1)}"
        case ("l2b_decision", "decision_done"):
            return (
                f"  L2B 完成 ({p.get('elapsed_sec', 0):.1f}s)："
                f"保留 {p.get('keep_count', 0)}，删除 {p.get('cut_count', 0)}，"
                f"共 {p.get('total', 0)} 句"
            )
        # ── L2C ──────────────────────────────────────────────────────────
        case ("l2c_review", "review_start"):
            return f"  审核中（轮次 {p.get('review_round', 0)}）..."
        case ("l2c_review", "review_done"):
            verdict = p.get("verdict", "")
            rate = p.get("must_pass_rate", "")
            fix = p.get("fix_count", 0)
            return (
                f"  L2C 完成 ({p.get('elapsed_sec', 0):.1f}s)："
                f"verdict={verdict} must通过率={rate} 修正={fix}条"
            )
        # ── L2D ──────────────────────────────────────────────────────────
        case ("l2d_human", "waiting_input"):
            return "  等待人工审阅..."
        case ("l2d_human", "action_processed"):
            msg = p.get("result_message", "")
            return f"  操作已处理{': ' + msg if msg else ''}"
        # ── L3 Execute ────────────────────────────────────────────────────
        case ("l3_execute", "resolve_start"):
            return f"  L3 编排中（{p.get('segment_count', 0)} 个保留段）..."
        case ("l3_execute", "vad_snap_start"):
            return f"  静音吸附中（snap_radius={p.get('snap_radius', 0):.3f}s）..."
        case ("l3_execute", "vad_snap_done"):
            return (
                f"  静音吸附完成：{p.get('silence_count', 0)} 条静音区间，"
                f"snap_radius={p.get('snap_radius', 0):.3f}s"
                f"（{p.get('elapsed_sec', 0):.1f}s）"
            )
        case ("l3_execute", "render_start"):
            return f"  开始渲染（{p.get('segment_count', 0)} 个 cut 单元）..."
        case ("l3_execute", "render_progress"):
            done = p.get("done", 0)
            total = p.get("total", 1)
            pct = done / total * 100 if total else 0
            bar_width = 20
            filled = int(pct / 100 * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            return f"  渲染 {bar} {done}/{total}  ({pct:.0f}%)"
        case ("l3_execute", "render_done"):
            return f"  渲染完成 ({p.get('elapsed_sec', 0):.1f}s)"
        case ("l3_execute", "encode_start"):
            return f"  编码中（{p.get('segment_count', 0)} 段）..."
        case ("l3_execute", "encode_progress"):
            idx = p.get("segment_index", 0)
            total = p.get("total_segments", 1)
            return f"  编码进度 {idx + 1}/{total}"
        case ("l3_execute", "assemble_start"):
            return "  合并片段中..."
        case ("l3_execute", "execute_done"):
            out = p.get("output_path", "")
            return f"  L3 完成 ({p.get('elapsed_sec', 0):.1f}s) → {out}"
        # ── LLM 流式（所有 L2 节点共用）────────────────────────────────────
        case (_, "llm_stream"):
            evt = p.get("event", "")
            stage = p.get("stage", "")
            match evt:
                case "reasoning_delta" | "content_delta":
                    # CLI 不逐 delta 打印（太碎），静默
                    return ""
                case "retry":
                    attempt = p.get("attempt", 0)
                    reason = p.get("retry_reason", "")
                    reason_short = reason[:60] + "…" if len(reason) > 60 else reason
                    return f"  [LLM] {stage} 第 {attempt} 次失败，重试中... ({reason_short})"
                case _:
                    return ""
        # ── 未知 ──────────────────────────────────────────────────────────
        case _:
            return f"  [{node_id}] {phase}"


# 向后兼容别名（历史代码可能引用）
_format_progress = format_progress
_format_review_summary = format_review_summary
_format_decision_list = format_decision_list
_format_stats = format_stats
_parse_l1a_intra_chunk_progress = parse_l1a_intra_chunk_progress
_parse_l1a_chunk_done = parse_l1a_chunk_done

__all__ = [
    "L1aChunkProgressState",
    "L1aChunkDoneState",
    "parse_l1a_intra_chunk_progress",
    "parse_l1a_chunk_done",
    "format_review_summary",
    "format_decision_list",
    "format_stats",
    "format_progress",
]
