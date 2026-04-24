"""L2dNode — 人工审阅节点。

调用 intelligence_2d_core.run_2d() 处理用户操作。
通过 ctx.pending_action 队列等待用户输入。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from autosmartcut.intelligence_2d_core import (
    AcceptAction,
    QuitAction,
    ShowAction,
    Signal,
    run_2d,
)
from autosmartcut.pipeline_events import NeedInputEvent, ProgressEvent
from autosmartcut.pipeline_models import L2dOutput, StageResult, StageStatus

if TYPE_CHECKING:
    from autosmartcut.config import AppConfig
    from autosmartcut.pipeline_models import StageContext

logger = logging.getLogger(__name__)


class L2dNode:
    """L2D：人工审阅节点（交互循环）。"""

    id = "l2d_human"
    reads = frozenset({"keep_mask", "review_report", "comprehension"})
    # keep_mask 是就地覆盖写（合并 overrides），不在 DAG writes 中声明以避免与 l2b_decision 冲突
    # l2d_completed 作为专属输出标识，供 l3_execute 建立 DAG 依赖
    writes = frozenset({"human_feedback_history", "l2d_completed"})
    phase = 2
    resumable = True

    def __init__(self, config: "AppConfig") -> None:
        self._config = config

    async def run(self, ctx: "StageContext") -> StageResult:
        """交互循环：等待用户操作，根据 signal 决定 CONTINUE/DONE/QUIT/REFLOW。"""
        manifest = ctx.manifest

        if ctx.pending_action is None:
            # 无交互队列：自动确认（CLI auto 模式）
            logger.info("[L2dNode] 无 pending_action 队列，自动确认（auto 模式）")
            core_result = run_2d(manifest, AcceptAction())
            manifest.update(core_result.manifest_dict)
            return self._make_done_result(manifest)

        # 初始展示
        core_result = run_2d(manifest, ShowAction())
        manifest.update(core_result.manifest_dict)

        reflow_count = 0

        while True:
            # 发布 NeedInputEvent，通知消费层渲染审阅界面
            ctx.emit(NeedInputEvent(
                node_id=self.id,
                display=core_result.display_data,
            ))

            # 等待用户操作
            action = await ctx.pending_action.get()

            # 处理操作
            core_result = run_2d(manifest, action)
            manifest.update(core_result.manifest_dict)

            signal = core_result.signal

            if signal == Signal.CONTINUE:
                # 继续等待下一个操作
                ctx.emit(ProgressEvent(
                    node_id=self.id,
                    message=core_result.message or "操作已处理，等待下一步...",
                ))
                continue

            elif signal == Signal.DONE:
                # 用户确认，流转 Layer 3
                return self._make_done_result(manifest)

            elif signal == Signal.QUIT:
                # 用户取消
                logger.info("[L2dNode] 用户取消")
                return StageResult(
                    status=StageStatus.FAILED,
                    summary="用户取消（QuitAction）",
                )

            elif signal == Signal.REFLOW_2A:
                # F1/F2 反馈，回流至 2a
                reflow_count += 1
                logger.info("[L2dNode] 回流至 l2a_comprehension（#%d）", reflow_count)
                return StageResult(
                    status=StageStatus.REFLOW,
                    reflow_target="l2a_comprehension",
                    summary=f"回流至 l2a_comprehension（{core_result.message}）",
                )

            elif signal == Signal.REFLOW_2B:
                # F3 反馈，回流至 2b
                reflow_count += 1
                logger.info("[L2dNode] 回流至 l2b_decision（#%d）", reflow_count)
                # 将 F3 的 selection_opinion 注入 manifest 供 L2bNode 读取
                reflow_ctx = manifest.get("_reflow_context", {})
                if reflow_ctx.get("feedback_type") == "f3_selection_opinion":
                    manifest["_selection_opinion"] = reflow_ctx.get(
                        "feedback_payload", {}
                    ).get("text", "")
                return StageResult(
                    status=StageStatus.REFLOW,
                    reflow_target="l2b_decision",
                    summary=f"回流至 l2b_decision（{core_result.message}）",
                )

            else:
                # 未知信号，继续等待
                logger.warning("[L2dNode] 未知信号: %s，继续等待", signal)
                continue

    def _make_done_result(self, manifest: dict) -> StageResult:
        """构造 DONE 结果，统计最终 keep/cut 数量，并写入 l2d_completed 标识。"""
        keep_mask = manifest.get("keep_mask", [])
        keep_count = sum(1 for e in keep_mask if e.get("keep") is True)
        cut_count = sum(1 for e in keep_mask if e.get("keep") is False)
        override_count = len(manifest.get("_2d_overrides", []))
        feedback_history = manifest.get("human_feedback_history", [])
        reflow_count = sum(
            1 for h in feedback_history
            if h.get("feedback_type") in ("f1_purpose_drift", "f2_keyword_error", "f3_selection_opinion")
        )
        # 写入 l2d_completed 标识，供 l3_execute 的 DAG 依赖推导使用
        manifest["l2d_completed"] = True
        return StageResult(
            status=StageStatus.SUCCESS,
            summary=f"keep={keep_count} cut={cut_count} overrides={override_count} reflows={reflow_count}",
        )

    def summarize(self, manifest: dict) -> L2dOutput:
        keep_mask = manifest.get("keep_mask", [])
        keep_count = sum(1 for e in keep_mask if e.get("keep") is True)
        cut_count = sum(1 for e in keep_mask if e.get("keep") is False)
        feedback_history = manifest.get("human_feedback_history", [])
        override_count = len(set(
            o["index"]
            for h in feedback_history
            for o in h.get("overrides", [])
        ))
        reflow_count = sum(
            1 for h in feedback_history
            if h.get("feedback_type") in ("f1_purpose_drift", "f2_keyword_error", "f3_selection_opinion")
        )
        return L2dOutput(
            final_keep_count=keep_count,
            final_cut_count=cut_count,
            override_count=override_count,
            reflow_count=reflow_count,
        )
