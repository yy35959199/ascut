"""session_factory.py — 流水线 session 构造服务层。

提供 ``PipelineParams`` 数据类和 ``build_session()`` 工厂函数。
不依赖任何 CLI 框架（argparse / Typer），可被 CLI、TUI、GUI 直接调用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autosmartcut.config import AppConfig
    from autosmartcut.pipeline.pipeline_run import PipelineRun
    from autosmartcut.pipeline.pipeline_session import PipelineSession


# ---------------------------------------------------------------------------
# PipelineParams — session 构造所需的全部参数
# ---------------------------------------------------------------------------

@dataclass
class PipelineParams:
    """构造流水线所需的全部参数，UI 无关。

    调用方（CLI / TUI / GUI）负责将用户输入转换为此结构，
    然后传给 ``build_session()``。
    """

    # ── 输入源（二选一）────────────────────────────────────────────────────
    input_video: Path | None = None
    """新建工程时的输入视频路径。与 manifest_path 二选一。"""

    manifest_path: Path | None = None
    """续跑时的清单路径。与 input_video 二选一。"""

    # ── 执行参数 ────────────────────────────────────────────────────────────
    stage: str = "123"
    """stage 规格字符串，如 "1"、"23"、"123"、"1a" 等。"""

    goal: str = ""
    """智能层剪辑意图（L2 使用）。"""

    # ── 输出 ────────────────────────────────────────────────────────────────
    output_dir: Path | None = None
    """产物目录；含 L1 且省略时自动生成 ascut_out_<YYYY-mm-DD_HH-MM-ss.SSS>（冲突追加 _01）。"""

    output_name: str | None = None
    """输出视频文件名（basename），落在 output_dir。"""

    # ── 配置覆盖 ────────────────────────────────────────────────────────────
    config_path: Path | None = None
    """config.toml 路径；None 时使用默认路径。"""

    asr_model: Path | None = None
    """覆盖 config.models.asr_model_path。"""

    forced_aligner: Path | None = None
    """覆盖 config.models.forced_aligner_path。"""

    backend: str | None = None
    """覆盖 config.models.backend（"transformers" 或 "vllm"）。"""

    two_b_mode: str | None = None
    """覆盖 config.intelligence.two_b_mode（"single" 或 "block"）。"""

    # ── L3 参数 ─────────────────────────────────────────────────────────────
    pre_pad: float = 0.15
    post_pad: float = 0.25
    min_duration: float = 1.0
    no_vad_snap: bool = False

    # ── 其他 ────────────────────────────────────────────────────────────────
    device: str = "cuda:0"
    dtype: str = "float16"
    language: str = "Chinese"
    gpu_memory_utilization: float = 0.8
    interactive_2d: bool = False
    verbose: bool = False

    # ── 强制重跑 / 续跑模式 ─────────────────────────────────────────────────
    resume_mode: bool = False
    """True=续跑模式（跳过已完成节点）；False=重跑模式（stage 内节点无条件执行，默认）。"""

    from_node: str | None = None
    """L2 子阶段起点。合法值: "2a" / "2b" / "2c" / "2d" / None。

    None 表示从 stage_filter 决定的起点开始（默认行为）。
    非 None 时，该节点之前的同 phase 节点将被跳过（不执行）。

    前置条件（由 build_session 校验）：
    - stage_filter 必须包含 phase 2
    - from_node 对应节点之前的所有 L2 节点必须已完成（manifest 中有数据）
    """


# ---------------------------------------------------------------------------
# build_session — 工厂函数
# ---------------------------------------------------------------------------

def build_session(
    params: PipelineParams,
) -> "tuple[PipelineRun, PipelineSession, AppConfig]":
    """从结构化参数构造 PipelineRun + PipelineSession。

    不依赖 argparse / Typer / 任何 CLI 框架。

    内部流程：
    1. load_config(params.config_path) + 覆盖 config 字段
    2. 构造 PipelineRun（新建 or from_manifest）
    3. validate_manifest_for_stages（前置校验）
    4. PipelineSession.parse_stage_arg(params.stage) → stage_filter（第二项恒为 None）
    5. 构造 PipelineSession + register_default_nodes()

    Args:
        params: 构造参数。

    Returns:
        (PipelineRun, PipelineSession, AppConfig) 三元组。

    Raises:
        FileNotFoundError: 清单或视频不存在。
        ValueError: 参数校验失败（如 stage 非法、清单缺少必要字段）。
    """
    from autosmartcut.config import load_config
    from autosmartcut.manifest.manifest_io import load_manifest, validate_manifest_for_stages
    from autosmartcut.pipeline.pipeline_run import PipelineRun
    from autosmartcut.pipeline.pipeline_session import PipelineSession

    # ── 1. 加载配置 + 覆盖 ──────────────────────────────────────────────────
    cfg = load_config(params.config_path)
    if params.asr_model is not None:
        cfg.models.asr_model_path = params.asr_model
    if params.forced_aligner is not None:
        cfg.models.forced_aligner_path = params.forced_aligner
    if params.backend is not None:
        cfg.models.backend = params.backend
    if params.two_b_mode is not None:
        cfg.intelligence.two_b_mode = params.two_b_mode

    # ── 2. 构造 PipelineRun ──────────────────────────────────────────────────
    run = _build_run(params)

    # ── 3. 前置校验 ──────────────────────────────────────────────────────────
    stage_filter, _ = PipelineSession.parse_stage_arg(params.stage)
    _validate_prereq_manifest(stage_filter, run.manifest_path)

    # ── 3b. from_node 前置校验 ───────────────────────────────────────────────
    if params.from_node is not None:
        _validate_from_node_prereqs(
            from_node=params.from_node,
            stage_filter=stage_filter,
            manifest_path=run.manifest_path,
        )

    # ── 4. 解析 stage ────────────────────────────────────────────────────────
    stage_filter, _ = PipelineSession.parse_stage_arg(params.stage)

    # ── 5. 构造 PipelineSession ──────────────────────────────────────────────
    session = PipelineSession(
        manifest_path=run.manifest_path,
        config=cfg,
        stage_filter=stage_filter,
        from_node=params.from_node,
        max_reflows=cfg.intelligence.two_d_max_reflows,
        resume_mode=params.resume_mode,
    )
    session.register_default_nodes()

    return run, session, cfg


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _build_run(params: PipelineParams) -> "PipelineRun":
    """根据 params 构造 PipelineRun（新建 or 续跑）。"""
    from autosmartcut.pipeline.pipeline_run import PipelineRun
    from autosmartcut.pipeline.pipeline_session import PipelineSession

    # 判断是否需要 L1（新建工程）
    stage_filter, _ = PipelineSession.parse_stage_arg(params.stage)
    l1_mode = _infer_l1_mode(params.stage, stage_filter)

    if l1_mode == "both":
        # 新建工程（含阶段 1）
        if params.input_video is None:
            raise ValueError("当 stage 含阶段 1（L1）时必须提供 input_video")
        return PipelineRun.new(
            video_path=params.input_video,
            goal=params.goal or "",
            output_dir=params.output_dir,
            output_video_name=params.output_name,
        )
    else:
        # 续跑
        if params.manifest_path is None:
            raise ValueError("当 stage 不含阶段 1 时必须提供 manifest_path")
        mp = Path(params.manifest_path).resolve()
        od_arg = Path(params.output_dir).resolve() if params.output_dir else None
        if od_arg is not None and od_arg != mp.parent.resolve():
            return PipelineRun.fork(mp, od_arg, output_video_name=params.output_name)
        return PipelineRun.from_manifest(
            mp,
            goal_override=params.goal if params.goal else None,
            output_dir=params.output_dir,
            output_video_name=params.output_name,
        )


def _infer_l1_mode(stage_str: str, stage_filter: frozenset[int]) -> str:
    """从 stage 字符串推断 L1 模式。

    Returns:
        "both"（需要新建工程、走 L1）| "none"（仅续跑 manifest）
    """
    _L1_MODE_BY_SPEC: dict[str, str] = {
        "1": "both",
        "2": "none",
        "3": "none",
        "12": "both",
        "23": "none",
        "123": "both",
    }
    return _L1_MODE_BY_SPEC.get(stage_str, "both" if 1 in stage_filter else "none")


def _validate_prereq_manifest(
    stages: frozenset[int], manifest_path: "Path"
) -> None:
    """按即将执行的阶段校验清单前置条件。"""
    from autosmartcut.manifest.manifest_io import load_manifest, validate_manifest_for_stages

    need: frozenset[int] = frozenset()
    if 2 in stages and 1 not in stages:
        need |= {2}
    if 3 in stages and 2 not in stages:
        need |= {3}
    if need:
        data = load_manifest(manifest_path)
        validate_manifest_for_stages(need, data)


# ---------------------------------------------------------------------------
# from_node 校验辅助
# ---------------------------------------------------------------------------

# L2 节点顺序（与 PipelineSession._L2_TOPO_ORDER 一致）
_L2_TOPO_ORDER = (
    "l2a_comprehension",
    "l2b_decision",
    "l2c_review",
    "l2d_human",
)

_FROM_NODE_MAP: dict[str, str] = {
    "2a": "l2a_comprehension",
    "2b": "l2b_decision",
    "2c": "l2c_review",
    "2d": "l2d_human",
}

# 每个节点被跳过时，下游需要的字段（即该节点的 writes）
_FROM_NODE_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "l2a_comprehension": frozenset({"comprehension"}),
    "l2b_decision":      frozenset({"keep_mask"}),
    "l2c_review":        frozenset({"review_report"}),
    "l2d_human":         frozenset({"human_feedback_history", "l2d_completed"}),
}


def _validate_from_node_prereqs(
    from_node: str,
    stage_filter: "frozenset[int]",
    manifest_path: "Path",
) -> None:
    """校验 from_node 的前置条件。

    校验规则：
    1. from_node 必须是合法值（"2a"/"2b"/"2c"/"2d"）
    2. stage_filter 必须包含 phase 2（否则 from_node 无意义）
    3. from_node 对应节点之前的所有 L2 节点必须已完成
       （layer_status 有 completed 记录，且 current 中有对应数据字段）

    Raises:
        ValueError: 任何校验失败
    """
    if from_node not in _FROM_NODE_MAP:
        raise ValueError(
            f"非法 --from-node {from_node!r}；"
            f"合法值: {', '.join(sorted(_FROM_NODE_MAP))}"
        )

    if 2 not in stage_filter:
        raise ValueError(
            f"--from-node {from_node!r} 需要 --stage 包含阶段 2"
        )

    target_id = _FROM_NODE_MAP[from_node]

    # 找出需要被跳过的前置节点
    nodes_to_skip: list[str] = []
    for nid in _L2_TOPO_ORDER:
        if nid == target_id:
            break
        nodes_to_skip.append(nid)

    if not nodes_to_skip:
        # from_node="2a"，无需跳过任何节点，直接通过
        return

    # 加载清单，检查前置节点的完成状态和数据完整性
    from autosmartcut.manifest.manifest_io import load_manifest, ls_get_run_status
    data = load_manifest(manifest_path)
    current = data.get("current", {})
    if not isinstance(current, dict):
        current = {}

    for nid in nodes_to_skip:
        # 检查 layer_status
        status = ls_get_run_status(data, nid)
        if status != "completed":
            raise ValueError(
                f"--from-node {from_node!r} 要求节点 {nid!r} 已完成，"
                f"但当前状态为 {status!r}。"
                f"请先执行完整的 L2 流程，或从更早的子阶段开始。"
            )

        # 检查数据字段完整性
        required = _FROM_NODE_REQUIRED_FIELDS.get(nid, frozenset())
        for field_name in required:
            if field_name not in current:
                raise ValueError(
                    f"--from-node {from_node!r} 要求 current.{field_name!r} 存在，"
                    f"但清单中缺少该字段（节点 {nid!r} 的产物）。"
                )
