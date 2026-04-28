"""stage 解析单测（原 manifest_stages，现逻辑在 session_factory + runner）。"""

import argparse
import warnings
from pathlib import Path

import pytest

from autosmartcut.pipeline.pipeline_session import PipelineSession
from autosmartcut.cli.runner import _resolve_stages, _validate_cli_args


# ---------------------------------------------------------------------------
# PipelineSession.parse_stage_arg — stage 字符串解析
# ---------------------------------------------------------------------------

def test_parse_stage_arg_123() -> None:
    sf, nf = PipelineSession.parse_stage_arg("123")
    assert sf == frozenset({1, 2, 3})
    assert nf is None


def test_parse_stage_arg_12() -> None:
    sf, nf = PipelineSession.parse_stage_arg("12")
    assert sf == frozenset({1, 2})
    assert nf is None


def test_parse_stage_arg_23() -> None:
    sf, nf = PipelineSession.parse_stage_arg("23")
    assert sf == frozenset({2, 3})
    assert nf is None


def test_parse_stage_arg_1() -> None:
    sf, nf = PipelineSession.parse_stage_arg("1")
    assert sf == frozenset({1})
    assert nf is None


def test_parse_stage_arg_invalid() -> None:
    with pytest.raises(ValueError):
        PipelineSession.parse_stage_arg("13")
    with pytest.raises(ValueError):
        PipelineSession.parse_stage_arg("21")


# ---------------------------------------------------------------------------
# _resolve_stages — runner.py 内联的 stage 解析
# ---------------------------------------------------------------------------

def test_resolve_stages_default() -> None:
    ns = argparse.Namespace(stage=None, from_stage=None)
    stages, l1_mode = _resolve_stages(ns)
    assert stages == frozenset({1, 2, 3})
    assert l1_mode == "both"


def test_resolve_stages_explicit() -> None:
    ns = argparse.Namespace(stage="12", from_stage=None)
    stages, l1_mode = _resolve_stages(ns)
    assert stages == frozenset({1, 2})
    assert l1_mode == "both"


def test_resolve_stages_from_stage_deprecated() -> None:
    ns = argparse.Namespace(stage=None, from_stage=2)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        stages, l1_mode = _resolve_stages(ns)
        assert stages == frozenset({2, 3})
        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)


def test_resolve_stages_conflict() -> None:
    ns = argparse.Namespace(stage="1", from_stage=2)
    with pytest.raises(ValueError, match="不可同时"):
        _resolve_stages(ns)


# ---------------------------------------------------------------------------
# _validate_cli_args — runner.py 内联的 CLI 校验
# ---------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser()


def test_validate_cli_stage1_requires_input() -> None:
    p = _parser()
    ns = argparse.Namespace(input=None, manifest=None)
    with pytest.raises(SystemExit):
        _validate_cli_args(frozenset({1, 2, 3}), "both", ns, p)


def test_validate_cli_stage1_forbids_manifest(tmp_path: Path) -> None:
    p = _parser()
    m = tmp_path / "timeline_manifest.json"
    m.write_text("{}", encoding="utf-8")
    ns = argparse.Namespace(input=tmp_path / "v.mp4", manifest=m)
    with pytest.raises(SystemExit):
        _validate_cli_args(frozenset({1}), "both", ns, p)


def test_validate_cli_stage23_requires_manifest() -> None:
    p = _parser()
    ns = argparse.Namespace(input=None, manifest=None)
    with pytest.raises(SystemExit):
        _validate_cli_args(frozenset({2, 3}), "none", ns, p)


def test_validate_cli_stage23_ok(tmp_path: Path) -> None:
    p = _parser()
    m = tmp_path / "timeline_manifest.json"
    m.write_text("{}", encoding="utf-8")
    ns = argparse.Namespace(input=None, manifest=m)
    _validate_cli_args(frozenset({2, 3}), "none", ns, p)
