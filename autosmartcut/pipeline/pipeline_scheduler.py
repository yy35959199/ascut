"""pipeline_scheduler.py — FixedScheduler 实现。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from autosmartcut.pipeline.pipeline_models import (
    PipelineSnapshot,
    SchedulerAction,
    SchedulerActionType,
)

if TYPE_CHECKING:
    from autosmartcut.config import AppConfig


class FixedScheduler:
    """MVP 调度策略。按 DAG 拓扑序执行，内置 2b-2c 循环规则和 2d 回流规则。"""

    def __init__(self, config: "AppConfig") -> None:
        self._config = config
        self._max_review_rounds = config.intelligence.two_c_max_review_rounds
        self._two_b_mode = config.intelligence.two_b_mode

    async def next_action(self, snapshot: PipelineSnapshot) -> SchedulerAction:
        """根据快照决定下一步调度动作。

        决策树（按优先级）：
        1. 无可调度节点且所有节点完成 → COMPLETE
        2. 无可调度节点但有节点运行中 → 等待（返回空 RUN_BATCH）
        3. l2b_decision 可调度且 last_review_verdict == "fix_decision"
           且 review_round < max_review_rounds → 重跑 l2b，注入 review_fixes
        4. l2b_decision 可调度且 last_review_verdict == "fix_decision"
           且 review_round >= max_review_rounds → 强制通过，注入 force_pass=True
        5. 其余情况：注入 two_b_mode/review_round 参数，按节点数返回 RUN_BATCH 或 RUN_NODE
        """
        schedulable = snapshot.schedulable_nodes

        # 1. 无可调度节点
        if not schedulable:
            if self._all_completed(snapshot):
                return SchedulerAction(action_type=SchedulerActionType.COMPLETE)
            # 有节点运行中，等待
            return SchedulerAction(
                action_type=SchedulerActionType.RUN_BATCH,
                node_ids=[],
            )

        # 3. l2c 返回 fix_decision 且未超轮次 → 重跑 l2b
        if (
            "l2b_decision" in schedulable
            and snapshot.last_review_verdict == "fix_decision"
            and snapshot.review_round < self._max_review_rounds
        ):
            return SchedulerAction(
                action_type=SchedulerActionType.RUN_NODE,
                node_ids=["l2b_decision"],
                params={
                    "review_round": snapshot.review_round,
                    "two_b_mode": self._two_b_mode,
                    "review_fixes": self._extract_review_fixes(snapshot),
                },
            )

        # 4. 达到最大审核轮次 → 强制通过
        if (
            "l2b_decision" in schedulable
            and snapshot.last_review_verdict == "fix_decision"
            and snapshot.review_round >= self._max_review_rounds
        ):
            return SchedulerAction(
                action_type=SchedulerActionType.RUN_NODE,
                node_ids=["l2b_decision"],
                params={
                    "review_round": snapshot.review_round,
                    "two_b_mode": self._two_b_mode,
                    "force_pass": True,
                },
            )

        # 5. 其余情况：注入 l2b 参数（若适用），按节点数调度
        params: dict = {}
        if "l2b_decision" in schedulable:
            params["two_b_mode"] = self._two_b_mode
            params["review_round"] = snapshot.review_round

        if len(schedulable) > 1:
            return SchedulerAction(
                action_type=SchedulerActionType.RUN_BATCH,
                node_ids=schedulable,
                params=params,
            )
        return SchedulerAction(
            action_type=SchedulerActionType.RUN_NODE,
            node_ids=schedulable,
            params=params,
        )

    def _all_completed(self, snapshot: PipelineSnapshot) -> bool:
        """所有节点均为 completed/skipped/failed（无 pending/running）。"""
        terminal = {"completed", "skipped", "failed"}
        return all(s.status in terminal for s in snapshot.node_states.values())

    def _extract_review_fixes(self, snapshot: PipelineSnapshot) -> list[dict]:
        """从 review_report 提取修正指令。

        L2bNode 会直接从 manifest["review_report"] 读取修正指令，
        因此此处返回空列表即可。
        """
        return []
