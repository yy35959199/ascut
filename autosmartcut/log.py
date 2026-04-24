"""流水线统一日志：loguru 双写（stderr + ``run_<run_id>.log``）+ stdlib ``logging`` 桥接。

- 文件 sink 使用 ``enqueue=True``，格式化与写盘在后台线程，减轻主线程阻塞。
- 大块 JSON（annotations、LLM 全文）使用 ``opt(lazy=True)`` + DEBUG，仅在 DEBUG sink（文件）上于后台序列化；终端默认 INFO，避免刷屏。
- ``logging.getLogger("autosmartcut.*")`` 经 ``InterceptHandler`` 转发到 loguru，业务代码可继续用标准库 API。
"""

from __future__ import annotations

import atexit
import json
import logging
import sys
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from loguru import logger as loguru_logger

from autosmartcut.manifest_io import load_manifest
from autosmartcut.pipeline_run import PipelineRun

_PACKAGE = "autosmartcut"

# 与旧 Formatter 风格接近：文件无颜色；终端可用简单格式（loguru 自带 level 颜色需 colorize=True）
_STDERR_FMT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)
_FILE_FMT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}\n"
)

_active_file_path: Path | None = None
_current_verbose: bool = False


def log_path_for_manifest(manifest_path: Path) -> Path:
    """与 ``PipelineRun.log_path`` 一致：``<清单父目录>/run_<run_id>.log``。"""
    mp = Path(manifest_path).resolve()
    if not mp.is_file():
        return mp.parent / "run_unknown.log"
    data = load_manifest(mp)
    run_id = str(data.get("run_id") or "unknown")
    return mp.parent / f"run_{run_id}.log"


class _InterceptHandler(logging.Handler):
    """将 ``logging`` 记录转发到 loguru（保留异常栈）。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = loguru_logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        depth = 6
        try:
            loguru_logger.opt(depth=depth, exception=record.exc_info).log(
                level, record.getMessage()
            )
        except Exception:
            self.handleError(record)


def _configure_stdlib_bridge() -> None:
    root = logging.getLogger(_PACKAGE)
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    root.propagate = False
    root.addHandler(_InterceptHandler())


def _install_sinks(
    *,
    file_path: Path | None,
    verbose: bool,
    banner: str | None = None,
    suppress_stderr: bool = False,
) -> None:
    """安装 loguru：终端 INFO/DEBUG + 可选文件 DEBUG（enqueue）。

    suppress_stderr=True 时跳过 stderr sink，仅写文件。
    TUI 模式必须传 suppress_stderr=True：Textual 接管了终端 alternate screen
    buffer，若 loguru 继续往 stderr 写则会直接覆盖 Textual 的渲染输出。
    """
    global _active_file_path, _current_verbose

    loguru_logger.remove()
    if not suppress_stderr:
        stderr_level = "DEBUG" if verbose else "INFO"
        loguru_logger.add(
            sys.stderr,
            level=stderr_level,
            format=_STDERR_FMT,
            colorize=True,
            enqueue=False,
        )
    if file_path is not None:
        fp = Path(file_path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        loguru_logger.add(
            str(fp),
            level="DEBUG",
            format=_FILE_FMT,
            encoding="utf-8",
            enqueue=True,
        )
        _active_file_path = fp.resolve()
    else:
        _active_file_path = None
    _current_verbose = verbose
    _configure_stdlib_bridge()
    if banner:
        loguru_logger.info(banner)


def _shutdown_loguru() -> None:
    try:
        loguru_logger.remove()
    except Exception:
        pass


atexit.register(_shutdown_loguru)


def setup_logging(run: PipelineRun, *, verbose: bool = False) -> None:
    """CLI 全流程：stderr + ``output_dir/run_<run_id>.log``。"""
    started = run.started_at.strftime("%Y-%m-%d %H:%M:%S")
    banner = (
        f"=== AutoSmartCut 开始 | run_id={run.run_id} | started_at={started} | "
        f"input={run.video_path} | log={run.log_path} ==="
    )
    _install_sinks(file_path=run.log_path, verbose=verbose, banner=banner)


def setup_logging_tui(run: PipelineRun, *, verbose: bool = False) -> None:
    """TUI 模式：仅写文件，不挂 stderr sink。

    Textual 接管了终端 alternate screen buffer，任何往 stderr/stdout 的输出
    都会覆盖 TUI 渲染。TUI 内的日志通过两条路径展示：
      1. EventBus LogEvent → LogArea（pipeline 节点主动 emit 的日志）
      2. loguru TUI sink → LogArea（由 PipelineApp.on_mount 注册，覆盖所有日志）
    """
    started = run.started_at.strftime("%Y-%m-%d %H:%M:%S")
    banner = (
        f"=== AutoSmartCut TUI 开始 | run_id={run.run_id} | started_at={started} | "
        f"input={run.video_path} | log={run.log_path} ==="
    )
    _install_sinks(
        file_path=run.log_path,
        verbose=verbose,
        banner=banner,
        suppress_stderr=True,
    )


def setup_logging_for_manifest(manifest_path: Path, *, verbose: bool = False) -> None:
    """仅清单续跑 / 单独 L2：与清单同目录的 ``run_<run_id>.log``；若与当前文件 sink 相同则尽量不重复 remove。"""
    lp = log_path_for_manifest(manifest_path)
    global _active_file_path, _current_verbose
    if (
        _active_file_path is not None
        and _active_file_path == lp.resolve()
        and _current_verbose == verbose
    ):
        return
    mp = Path(manifest_path).resolve()
    rid = "unknown"
    if mp.is_file():
        try:
            rid = str(load_manifest(mp).get("run_id") or "unknown")
        except (OSError, ValueError):
            pass
    banner = (
        f"=== AutoSmartCut 清单模式 | run_id={rid} | manifest={mp} | log={lp} ==="
    )
    _install_sinks(file_path=lp, verbose=verbose, banner=banner)


def attach_stderr_if_unconfigured(*, verbose: bool = False) -> None:
    """未配置任何 sink 时仅挂 stderr（兜底）。"""
    # loguru 无公开「是否有 handler」API，用 _core.handlers 长度判断
    try:
        if len(loguru_logger._core.handlers) > 0:  # type: ignore[attr-defined]
            return
    except Exception:
        pass
    _install_sinks(file_path=None, verbose=verbose, banner=None)


def ensure_autosmartcut_logging(*, verbose: bool = False) -> None:
    """兼容旧名。"""
    attach_stderr_if_unconfigured(verbose=verbose)


def _safe_params(params: dict[str, Any]) -> dict[str, Any]:
    """阶段入参摘要（避免不可序列化或过大对象直接进 INFO）。"""
    out: dict[str, Any] = {}
    for k, v in params.items():
        if isinstance(v, Path):
            out[k] = str(v)
        elif isinstance(v, (str, int, float, bool, type(None))):
            out[k] = v
        elif isinstance(v, (list, tuple)):
            out[k] = f"<{type(v).__name__} len={len(v)}>"
        elif isinstance(v, dict):
            out[k] = f"<dict keys={list(v.keys())[:10]}>"
        else:
            out[k] = repr(v)[:200]
    return out


@contextmanager
def log_stage(stage_id: str, **params: Any) -> Generator[None, None, None]:
    """阶段：开始时打阶段名+入参摘要；结束时打耗时；异常时打错误并上抛。"""
    slog = logging.getLogger(_PACKAGE)
    slog.info("[%s] 开始 | params=%s", stage_id, _safe_params(params))
    t0 = time.perf_counter()
    err: BaseException | None = None
    try:
        yield
    except BaseException as e:
        err = e
        raise
    finally:
        dt = time.perf_counter() - t0
        if err is None:
            slog.info("[%s] 结束 | elapsed_s=%.3f", stage_id, dt)
        else:
            slog.error(
                "[%s] 结束(异常) | elapsed_s=%.3f | error=%s",
                stage_id,
                dt,
                err,
                exc_info=err is not None and not isinstance(err, KeyboardInterrupt),
            )


def log_lazy_json(tag: str, label: str, factory: Callable[[], Any]) -> None:
    """大块 JSON：lazy + DEBUG，主线程不执行 ``json.dumps``；仅写入带 DEBUG 的 sink（当前为文件）。"""
    # lazy=True 时，每个 ``{}`` 占位符对应一个 **可调用** 参数；整段合并为一个 lambda。
    loguru_logger.opt(lazy=True).debug(
        "{}",
        lambda: f"[{tag}] {label}\n{json.dumps(factory(), ensure_ascii=False, indent=2)}",
    )


def log_stage_result(stage_id: str, *, summary: str | None = None) -> None:
    """阶段结束后在终端与文件各打一行摘要（INFO）。"""
    if summary:
        logging.getLogger(_PACKAGE).info("[%s] 输出摘要 | %s", stage_id, summary)
