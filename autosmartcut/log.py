"""流水线统一日志：stderr + run_<ULID>.log。"""

from __future__ import annotations

import logging
import sys
from autosmartcut.pipeline_run import PipelineRun

_LOG_FORMAT = "[%(asctime)s][%(filename)s:%(lineno)d][%(levelname)s] %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def _root_logger() -> logging.Logger:
	return logging.getLogger("autosmartcut")


def ensure_autosmartcut_logging(*, verbose: bool = False) -> None:
	"""若尚未配置 autosmartcut 日志，则仅向 stderr 输出（供单独调用各层入口时使用）。"""
	log = _root_logger()
	if log.handlers:
		return
	level = logging.DEBUG if verbose else logging.INFO
	log.setLevel(level)
	h = logging.StreamHandler(sys.stderr)
	h.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FMT))
	log.addHandler(h)
	log.propagate = False


def setup_logging(run: PipelineRun, *, verbose: bool = False) -> None:
	"""配置 autosmartcut 命名空间日志：stderr + output_dir/run_<run_id>.log。"""
	log = _root_logger()
	log.handlers.clear()
	level = logging.DEBUG if verbose else logging.INFO
	log.setLevel(level)
	log.propagate = False

	fmt = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FMT)

	stderr_h = logging.StreamHandler(sys.stderr)
	stderr_h.setFormatter(fmt)
	log.addHandler(stderr_h)

	run.log_path.parent.mkdir(parents=True, exist_ok=True)
	file_h = logging.FileHandler(run.log_path, encoding="utf-8")
	file_h.setFormatter(fmt)
	log.addHandler(file_h)

	started = run.started_at.strftime("%Y-%m-%d %H:%M:%S")
	log.info(
		"=== AutoSmartCut 开始 | run_id=%s | started_at=%s | input=%s ===",
		run.run_id,
		started,
		run.video_path,
	)
