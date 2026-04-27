"""pipeline_session.py — DAG 调度核心。

负责节点注册、拓扑排序、并行批次调度、事件总线、检查点管理。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from autosmartcut.manifest_io import load_manifest, save_manifest
from autosmartcut.pipeline_events import (
    ErrorEvent,
    PausedEvent,
    PipelineCompleteEvent,
    PipelineEvent,
    StageEnterEvent,
    StageExitEvent,
)
from autosmartcut.pipeline_models import (
    NodeState,
    PipelineSnapshot,
    SchedulerAction,
    SchedulerActionType,
    StageContext,
    StageResult,
    StageStatus,
)
from autosmartcut.pipeline_protocols import (
    CyclicDependencyError,
    MissingManifestFieldError,
    Scheduler,
    StageNode,
)

if TYPE_CHECKING:
    from autosmartcut.config import AppConfig

logger = logging.getLogger("autosmartcut")


class PipelineSession:
    """DAG 调度核心。负责节点注册、拓扑排序、并行批次调度、事件总线、检查点管理。

    典型用法（CLI 模式）：
        session = PipelineSession(manifest_path, config, stage_filter={1,2,3})
        session.register_default_nodes()
        adapter = CLIAdapter(session)
        adapter.start_sync()

    典型用法（TUI 模式）：
        session = PipelineSession(manifest_path, config, stage_filter={1,2,3})
        session.register_default_nodes()
        adapter = TUIAdapter(session)
        await adapter.start_async()
    """

    def __init__(
        self,
        manifest_path: Path,
        config: "AppConfig",
        *,
        scheduler: "Scheduler | None" = None,
        stage_filter: "frozenset[int] | None" = None,
        max_reflows: int = 3,
    ) -> None:
        self._manifest_path = manifest_path
        self._config = config
        self._stage_filter = stage_filter
        self._max_reflows = max_reflows

        # Scheduler 延迟导入避免循环依赖
        if scheduler is not None:
            self._scheduler: Scheduler = scheduler
        else:
            from autosmartcut.pipeline_scheduler import FixedScheduler
            self._scheduler = FixedScheduler(config)

        self._nodes: dict[str, StageNode] = {}
        self._dag: dict[str, set[str]] = {}      # node_id → 依赖的前置节点集合
        self._node_states: dict[str, NodeState] = {}

        self._event_handlers: list[Callable[[PipelineEvent], None]] = []
        self._action_queue: asyncio.Queue = asyncio.Queue()

        self._pause_flag: bool = False
        self._abort_flag: bool = False
        self._reflow_count: int = 0
        self._review_round: int = 0
        self._last_review_verdict: str = ""

        # 供 abort() 使用的当前 manifest 引用
        self._current_manifest: dict = {}

    # -----------------------------------------------------------------------
    # 节点注册
    # -----------------------------------------------------------------------

    def register(self, node: StageNode) -> None:
        """注册单个节点。必须在 start_async() 之前调用。"""
        if node.id in self._nodes:
            raise ValueError(f"节点 {node.id!r} 已注册")
        self._nodes[node.id] = node
        self._node_states[node.id] = NodeState(node_id=node.id, status="pending")

    def register_default_nodes(self) -> None:
        """注册标准 6 个节点（快捷方法）。"""
        from autosmartcut.nodes import (
            L1Node,
            L2aNode,
            L2bNode,
            L2cNode,
            L2dNode,
            L3Node,
        )
        for node in [
            L1Node(self._config),
            L2aNode(self._config),
            L2bNode(self._config),
            L2cNode(self._config),
            L2dNode(self._config),
            L3Node(self._config),
        ]:
            self.register(node)

    # -----------------------------------------------------------------------
    # EventBus
    # -----------------------------------------------------------------------

    def subscribe(self, handler: Callable[[PipelineEvent], None]) -> None:
        """注册事件处理器。handler 在每次事件发布时被同步调用。
        可注册多个 handler（如同时注册 CLI 打印和日志记录）。
        """
        self._event_handlers.append(handler)

    def _emit(self, event: PipelineEvent) -> None:
        """向所有已注册的 handler 发布事件。"""
        for handler in self._event_handlers:
            try:
                handler(event)
            except Exception as e:
                # handler 异常不应中断流水线
                logger.warning("EventBus handler 异常: %s", e)

    # -----------------------------------------------------------------------
    # DAG 构建与拓扑排序（任务 2.2）
    # -----------------------------------------------------------------------

    def _build_dag(self) -> None:
        """根据节点的 reads/writes 字段自动推导有向依赖边，构建 DAG。

        算法：
        1. 构建 writes_map: field_name → node_id（写出该字段的节点）
        2. 对每个节点 B，遍历其 reads 字段：
           若 field 在 writes_map 中，则 writes_map[field] → B（B 依赖该节点）
        3. 拓扑排序验证无环（Kahn 算法）
        """
        writes_map: dict[str, str] = {}
        for node in self._nodes.values():
            for field in node.writes:
                if field in writes_map:
                    raise ValueError(
                        f"字段 {field!r} 被多个节点写出: "
                        f"{writes_map[field]!r} 和 {node.id!r}"
                    )
                writes_map[field] = node.id

        self._dag = {nid: set() for nid in self._nodes}
        for node in self._nodes.values():
            for field in node.reads:
                if field in writes_map:
                    dep = writes_map[field]
                    if dep != node.id:
                        self._dag[node.id].add(dep)

        # 拓扑排序验证无环（Kahn 算法）
        in_degree = {nid: len(deps) for nid, deps in self._dag.items()}
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        visited = 0
        while queue:
            nid = queue.pop(0)
            visited += 1
            for other_id, deps in self._dag.items():
                if nid in deps:
                    in_degree[other_id] -= 1
                    if in_degree[other_id] == 0:
                        queue.append(other_id)
        if visited != len(self._nodes):
            raise CyclicDependencyError("DAG 中存在环路，无法进行拓扑排序")

    def _get_downstream_nodes(self, node_id: str, inclusive: bool) -> list[str]:
        """获取 node_id 的所有下游节点（BFS）。

        Args:
            node_id: 起始节点 id
            inclusive: True 时包含 node_id 本身

        Returns:
            下游节点 id 列表（含或不含 node_id 本身）
        """
        result: list[str] = []
        visited: set[str] = set()
        queue = [node_id]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            if current != node_id or inclusive:
                result.append(current)
            # 找所有依赖 current 的节点（即 current 的下游）
            for other_id, deps in self._dag.items():
                if current in deps and other_id not in visited:
                    queue.append(other_id)
        return result

    # -----------------------------------------------------------------------
    # stage_filter 与 resumable skip（任务 2.6）
    # -----------------------------------------------------------------------

    def _apply_stage_filter(self, manifest: dict) -> None:
        """跳过 phase 不在 stage_filter 中的节点（标记为 skipped）。"""
        if self._stage_filter is None:
            return
        for node_id, node in self._nodes.items():
            if node.phase not in self._stage_filter:
                self._node_states[node_id] = NodeState(node_id=node_id, status="skipped")

    def _apply_resumable_skip(self, manifest: dict) -> None:
        """resumable=True 且 layer_status 有完成标记时跳过节点。

        跳过节点时，将 manifest["current"] 中对应节点写出的字段回填到顶层，
        确保后续节点能从内存 manifest 中读到这些字段（而不必重新执行）。
        """
        layer_status = manifest.get("layer_status", {})
        current = manifest.get("current", {})
        if not isinstance(current, dict):
            current = {}

        for node_id, node in self._nodes.items():
            # 只处理仍为 pending 的节点
            if self._node_states[node_id].status != "pending":
                continue
            if node.resumable and f"{node_id}_completed_at" in layer_status:
                self._node_states[node_id] = NodeState(
                    node_id=node_id,
                    status="skipped",
                    completed_at=datetime.now(),
                )
                # 将 current 中该节点写出的字段回填到顶层 manifest，
                # 确保下游节点能从内存中读到（不依赖重新执行）
                for field in self._L2_CURRENT_FIELDS:
                    if field in current and field not in manifest:
                        manifest[field] = current[field]

    @staticmethod
    def parse_stage_arg(stage_str: str) -> "tuple[frozenset[int], frozenset[str] | None]":
        """将 ``--stage`` 参数映射为 ``(stage_filter, node_id_filter)``。

        当前仅支持按 phase 过滤；``node_id_filter`` 恒为 ``None``（保留二元组返回值以兼容旧调用）。

        合法 ``stage_str`` 与 ``stage_filter`` 对应关系：

        - ``"1"`` → ``{1}``
        - ``"2"`` → ``{2}``
        - ``"3"`` → ``{3}``
        - ``"12"`` → ``{1, 2}``
        - ``"23"`` → ``{2, 3}``
        - ``"123"`` → ``{1, 2, 3}``
        """
        _MAP: dict[str, tuple[frozenset[int], frozenset[str] | None]] = {
            "1":    (frozenset({1}),     None),
            "2":    (frozenset({2}),     None),
            "3":    (frozenset({3}),     None),
            "12":   (frozenset({1, 2}),  None),
            "23":   (frozenset({2, 3}),  None),
            "123":  (frozenset({1, 2, 3}), None),
        }
        if stage_str not in _MAP:
            raise ValueError(f"非法 --stage 值: {stage_str!r}")
        return _MAP[stage_str]

    # -----------------------------------------------------------------------
    # 调度循环与节点执行（任务 2.4）
    # -----------------------------------------------------------------------

    async def start_async(self) -> None:
        """异步启动流水线。构建 DAG，应用 stage_filter，进入流式调度循环。

        流式调度模式：节点完成即触发重新调度，不等待同批次其他节点。
        使用 asyncio.wait(FIRST_COMPLETED) 替代 gather，任意节点完成就重新扫描
        schedulable 节点并立刻启动，彻底消除"慢节点阻塞快节点下游"的问题。
        """
        self._build_dag()
        manifest = load_manifest(self._manifest_path)
        self._current_manifest = manifest  # 供 abort() 使用

        self._apply_stage_filter(manifest)
        self._apply_resumable_skip(manifest)

        start_time = datetime.now()

        # running: task → node_id，追踪所有正在运行的节点任务
        running: dict[asyncio.Task, str] = {}
        # launched: 已启动的 node_id 集合，防止同一节点被重复启动
        launched: set[str] = set()

        while not self._abort_flag:
            # ── 暂停检查 ────────────────────────────────────────────────────
            if self._pause_flag and not running:
                self._emit(PausedEvent(
                    completed_nodes=self._completed_node_ids(),
                    checkpoint_path=str(self._manifest_path),
                ))
                break

            # ── 扫描可调度节点，启动新任务 ──────────────────────────────────
            snapshot = self._build_snapshot(manifest)
            action = await self._scheduler.next_action(snapshot)

            if action.action_type == SchedulerActionType.COMPLETE:
                output = str(manifest.get("output_video", self._manifest_path))
                elapsed = (datetime.now() - start_time).total_seconds()
                self._emit(PipelineCompleteEvent(output=output, elapsed_seconds=elapsed))
                break

            if action.action_type == SchedulerActionType.PAUSE:
                self._pause_flag = True
                # 不立即 break，等 running 任务完成后再发 PausedEvent

            # 启动所有新的可调度节点（跳过已启动的）
            for node_id in (action.node_ids or []):
                if node_id not in launched:
                    launched.add(node_id)
                    task = asyncio.create_task(
                        self._run_node(node_id, manifest, action.params)
                    )
                    running[task] = node_id

            # ── 等待任意一个节点完成 ─────────────────────────────────────────
            if not running:
                if self._all_done():
                    output = str(manifest.get("output_video", self._manifest_path))
                    elapsed = (datetime.now() - start_time).total_seconds()
                    self._emit(PipelineCompleteEvent(output=output, elapsed_seconds=elapsed))
                break

            done_tasks, _ = await asyncio.wait(
                running.keys(), return_when=asyncio.FIRST_COMPLETED
            )

            # ── 处理完成的节点 ───────────────────────────────────────────────
            for task in done_tasks:
                node_id = running.pop(task)
                try:
                    result = task.result()
                except Exception as e:
                    from autosmartcut.pipeline_models import StageResult, StageStatus
                    result = StageResult(
                        status=StageStatus.FAILED,
                        summary=str(e),
                        error=e,
                    )
                await self._handle_result(node_id, result, manifest)
                if self._abort_flag:
                    # 取消所有仍在运行的任务
                    for t in running:
                        t.cancel()
                    break

            # 回到循环顶部，重新扫描 schedulable 节点

    def start_sync(self) -> None:
        """同步启动流水线（供 CLIAdapter 使用）。内部调用 asyncio.run()。"""
        asyncio.run(self.start_async())

    async def _run_node(
        self,
        node_id: str,
        manifest: dict,
        params: dict,
    ) -> StageResult:
        """执行单个节点。params 通过 StageContext.params 传入，不写入 manifest。"""
        # l2d_human 节点走交互路径
        if node_id == "l2d_human":
            return await self._handle_interactive(node_id, manifest, params)

        node = self._nodes[node_id]
        self._node_states[node_id].status = "running"

        # 将 manifest_path 注入 params（节点需要知道输出目录）
        injected_params = dict(params) if params else {}
        injected_params["manifest_path"] = str(self._manifest_path)

        ctx = StageContext(
            manifest=manifest,
            config=self._config,
            emit=self._emit,
            params=injected_params,
            pending_action=None,
            stage_filter=self._stage_filter,
        )

        import time as _time
        self._emit(StageEnterEvent(node_id=node_id))
        _t_start = _time.monotonic()
        try:
            result = await node.run(ctx)
        except Exception as e:
            logger.exception("节点 %s 执行异常: %s", node_id, e)
            result = StageResult(
                status=StageStatus.FAILED,
                summary=str(e),
                error=e,
            )

        _elapsed = _time.monotonic() - _t_start

        # 尝试获取结构化摘要（summarize 失败时静默降级，不中止流水线）
        _output = None
        if result.status == StageStatus.SUCCESS:
            try:
                _output = node.summarize(manifest)
            except Exception as _e:
                logger.warning("节点 %s summarize() 失败，output 置 None: %s", node_id, _e)

        self._emit(StageExitEvent(
            node_id=node_id,
            status=result.status.value,
            summary=result.summary,
            output=_output,
            elapsed_sec=_elapsed,
        ))
        return result

    def _build_snapshot(self, manifest: dict) -> PipelineSnapshot:
        """构建 PipelineSnapshot。

        schedulable_nodes 推导条件：
        1. 自身状态为 "pending"
        2. 所有前置节点（self._dag[node_id]）状态为 "completed" 或 "skipped"
        """
        schedulable: list[str] = []
        done_or_skip = {"completed", "skipped"}

        for node_id, state in self._node_states.items():
            if state.status != "pending":
                continue
            deps = self._dag.get(node_id, set())
            if all(self._node_states[dep].status in done_or_skip for dep in deps):
                schedulable.append(node_id)

        return PipelineSnapshot(
            node_states=dict(self._node_states),
            manifest_keys=frozenset(manifest.keys()),
            schedulable_nodes=schedulable,
            reflow_count=self._reflow_count,
            review_round=self._review_round,
            last_review_verdict=self._last_review_verdict,
            stage_filter=self._stage_filter,
        )

    def _all_done(self) -> bool:
        """所有节点均为 completed 或 skipped 或 failed（无 pending/running）。"""
        terminal = {"completed", "skipped", "failed"}
        return all(s.status in terminal for s in self._node_states.values())

    def _completed_node_ids(self) -> list[str]:
        """返回所有已完成节点的 id 列表。"""
        return [
            nid for nid, s in self._node_states.items()
            if s.status == "completed"
        ]

    # -----------------------------------------------------------------------
    # pause / abort / resume（任务 2.7）
    # -----------------------------------------------------------------------

    def pause(self) -> None:
        """设置暂停标志。当前正在运行的节点完成后，流水线停止调度新节点。"""
        self._pause_flag = True

    def abort(self, save: bool = True) -> None:
        """立即停止调度。
        save=True：保存已完成阶段的 manifest 检查点。
        save=False：不保存（用于用户取消场景）。
        """
        self._abort_flag = True
        if save and self._current_manifest:
            try:
                self._sync_to_current(self._current_manifest)
                save_manifest(self._manifest_path, self._current_manifest, atomic=True)
            except Exception as e:
                logger.warning("abort 保存 manifest 失败: %s", e)

    def resume(self) -> None:
        """从最近检查点恢复。重置 pause/abort 标志，重新进入调度循环。
        已完成节点（layer_status 有标记）会被 _apply_resumable_skip() 跳过。
        """
        self._pause_flag = False
        self._abort_flag = False
        asyncio.run(self.start_async())

    # -----------------------------------------------------------------------
    # REFLOW 处理（任务 2.8）
    # -----------------------------------------------------------------------

    async def _handle_reflow(
        self,
        reflow_target: str,
        manifest: dict,
    ) -> None:
        """处理回流：重置目标节点及其所有下游节点，重新调度。

        REFLOW_2A（reflow_target="l2a_comprehension"）：
            重置 l2a_comprehension、l2b_decision、l2c_review、l2d_human

        REFLOW_2B（reflow_target="l2b_decision"）：
            重置 l2b_decision、l2c_review、l2d_human
        """
        if self._reflow_count >= self._max_reflows:
            # 达到上限，不执行回流，继续等待用户确认
            logger.warning(
                "已达到最大回流次数 %d，忽略本次回流请求（target=%s）",
                self._max_reflows,
                reflow_target,
            )
            return

        # 保存回流前检查点
        self._sync_to_current(manifest)
        save_manifest(self._manifest_path, manifest, atomic=True)
        self._reflow_count += 1
        self._review_round = 0

        nodes_to_reset = self._get_downstream_nodes(reflow_target, inclusive=True)
        for node_id in nodes_to_reset:
            self._node_states[node_id] = NodeState(node_id=node_id, status="pending")
            # 清除 manifest 中该节点写出的字段
            if node_id in self._nodes:
                node = self._nodes[node_id]
                for field in node.writes:
                    manifest.pop(field, None)
            # 清除 layer_status 中的完成标记
            ls = manifest.get("layer_status", {})
            ls.pop(f"{node_id}_completed_at", None)

        logger.info(
            "回流 #%d：重置节点 %s（target=%s）",
            self._reflow_count,
            nodes_to_reset,
            reflow_target,
        )

    # -----------------------------------------------------------------------
    # NEEDS_INPUT 交互循环与 send_action（任务 2.10）
    # -----------------------------------------------------------------------

    async def _handle_interactive(
        self,
        node_id: str,
        manifest: dict,
        params: dict,
    ) -> StageResult:
        """处理 NEEDS_INPUT 节点的交互循环。

        构造 StageContext（注入 pending_action 队列和 params），调用节点 run()。
        节点内部通过 await ctx.pending_action.get() 等待用户操作。
        """
        injected_params = dict(params) if params else {}
        injected_params["manifest_path"] = str(self._manifest_path)

        ctx = StageContext(
            manifest=manifest,
            config=self._config,
            emit=self._emit,
            params=injected_params,
            pending_action=self._action_queue,
            stage_filter=self._stage_filter,
        )
        import time as _time
        self._node_states[node_id].status = "running"
        self._emit(StageEnterEvent(node_id=node_id))
        node = self._nodes[node_id]
        _t_start = _time.monotonic()
        try:
            result = await node.run(ctx)
        except Exception as e:
            logger.exception("交互节点 %s 执行异常: %s", node_id, e)
            result = StageResult(
                status=StageStatus.FAILED,
                summary=str(e),
                error=e,
            )

        _elapsed = _time.monotonic() - _t_start
        _output = None
        if result.status == StageStatus.SUCCESS:
            try:
                _output = node.summarize(manifest)
            except Exception as _e:
                logger.warning("交互节点 %s summarize() 失败，output 置 None: %s", node_id, _e)

        self._emit(StageExitEvent(
            node_id=node_id,
            status=result.status.value,
            summary=result.summary,
            output=_output,
            elapsed_sec=_elapsed,
        ))
        return result

    def send_action(self, action: object) -> None:
        """供消费层将用户操作传递给等待中的 l2d_human 节点。

        线程安全：可从非 asyncio 线程调用（如 Textual 的 UI 线程）。
        内部通过 asyncio.Queue 传递给节点的 pending_action 队列。
        """
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(self._action_queue.put_nowait, action)
        except RuntimeError:
            self._action_queue.put_nowait(action)

    # -----------------------------------------------------------------------
    # 统一结果处理（任务 2.10）
    # -----------------------------------------------------------------------

    # L2 节点写出的顶层字段 → 需要同步到 manifest["current"] 的键名映射
    # 格式：顶层键 → current 子键（通常相同）
    _L2_CURRENT_FIELDS: tuple[str, ...] = (
        "keep_mask",
        "comprehension",
        "review_report",
        "human_feedback_history",
        "l2d_completed",
    )

    def _sync_to_current(self, manifest: dict) -> None:
        """将 L2 节点写出的顶层字段同步到 manifest["current"]。

        pipeline 内各节点直接写 manifest 顶层（如 manifest["keep_mask"]），
        但 execution / validate_manifest_for_stages 读的是 manifest["current"]["keep_mask"]。
        每次保存前调用此方法确保两者一致。
        """
        cur = manifest.setdefault("current", {})
        if not isinstance(cur, dict):
            manifest["current"] = {}
            cur = manifest["current"]
        for key in self._L2_CURRENT_FIELDS:
            if key in manifest:
                cur[key] = manifest[key]

    def _mark_node_completed(self, node_id: str, manifest: dict) -> None:
        """SUCCESS：更新 NodeState、layer_status、l2c 的 review 元数据。"""
        self._node_states[node_id].status = "completed"
        self._node_states[node_id].completed_at = datetime.now()
        ls = manifest.setdefault("layer_status", {})
        ls[f"{node_id}_completed_at"] = datetime.now().isoformat()
        if node_id == "l2c_review":
            report = manifest.get("review_report", {})
            self._last_review_verdict = report.get("verdict", "")
            self._review_round += 1

    def _persist_manifest(self, manifest: dict) -> None:
        """将 manifest 落盘；失败仅记录 warning，不中断流水线。"""
        try:
            save_manifest(self._manifest_path, manifest, atomic=True)
        except Exception as e:
            logger.warning("保存 manifest 失败（路径=%s）: %s", self._manifest_path, e)

    def _mark_node_failed(self, node_id: str, result: StageResult) -> None:
        """FAILED：更新状态、发 ErrorEvent、置中止标志。"""
        self._node_states[node_id].status = "failed"
        self._emit(ErrorEvent(
            node_id=node_id,
            error=str(result.error or result.summary),
        ))
        self._abort_flag = True

    async def _handle_result(
        self,
        node_id: str,
        result: StageResult,
        manifest: dict,
    ) -> None:
        """统一处理节点执行结果（SUCCESS / FAILED / REFLOW 等分支）。"""
        if result.status == StageStatus.SUCCESS:
            self._mark_node_completed(node_id, manifest)
            self._sync_to_current(manifest)
            self._persist_manifest(manifest)
            node = self._nodes[node_id]
            try:
                self._node_states[node_id].output = node.summarize(manifest)
            except Exception as e:
                logger.warning("节点 %s summarize() 异常: %s", node_id, e)

        elif result.status == StageStatus.FAILED:
            self._mark_node_failed(node_id, result)

        elif result.status == StageStatus.REFLOW:
            self._node_states[node_id].status = "completed"  # l2d 本次执行完成
            await self._handle_reflow(result.reflow_target, manifest)

        elif result.status == StageStatus.NEEDS_INPUT:
            # NEEDS_INPUT 由 _handle_interactive 内部处理，此处不应出现
            # 但若出现则记录警告
            logger.warning("节点 %s 返回 NEEDS_INPUT，但未通过交互路径处理", node_id)
