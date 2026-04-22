"""manifest_stages 单测。"""

import argparse
import warnings
from pathlib import Path

import pytest

from autosmartcut.manifest_stages import (
    infer_l1_mode,
    parse_stage_spec,
    resolve_stages,
    validate_cli_args,
)


def test_parse_stage_spec() -> None:
    assert parse_stage_spec("123") == frozenset({1, 2, 3})
    assert parse_stage_spec("12") == frozenset({1, 2})
    assert parse_stage_spec("23") == frozenset({2, 3})
    assert parse_stage_spec("1") == frozenset({1})
    assert parse_stage_spec("1a2") == frozenset({1, 2})
    assert parse_stage_spec("1b2") == frozenset({2})
    assert parse_stage_spec("1b") == frozenset()


def test_infer_l1_mode_fallback() -> None:
    ns = argparse.Namespace()
    assert infer_l1_mode(ns, frozenset({2, 3})) == "none"
    assert infer_l1_mode(ns, frozenset({1})) == "both"


def test_resolve_stages_1a_sets_l1_mode() -> None:
    ns = argparse.Namespace(stage="1a2", from_stage=None)
    assert resolve_stages(ns) == frozenset({1, 2})
    assert getattr(ns, "_l1_mode") == "a"


def test_parse_stage_spec_invalid() -> None:
    with pytest.raises(ValueError):
        parse_stage_spec("13")
    with pytest.raises(ValueError):
        parse_stage_spec("21")


def test_resolve_stages_default() -> None:
    ns = argparse.Namespace(stage=None, from_stage=None)
    assert resolve_stages(ns) == frozenset({1, 2, 3})


def test_resolve_stages_explicit() -> None:
    ns = argparse.Namespace(stage="12", from_stage=None)
    assert resolve_stages(ns) == frozenset({1, 2})


def test_resolve_stages_from_stage_deprecated() -> None:
    ns = argparse.Namespace(stage=None, from_stage=2)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        out = resolve_stages(ns)
        assert out == frozenset({2, 3})
        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)


def test_resolve_stages_conflict() -> None:
    ns = argparse.Namespace(stage="1", from_stage=2)
    with pytest.raises(ValueError, match="不可同时"):
        resolve_stages(ns)


def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser()


def test_validate_cli_stage1_requires_input(tmp_path: Path) -> None:
    p = _parser()
    ns = argparse.Namespace(
        stage="1",
        from_stage=None,
        input=None,
        manifest=None,
        layer1_json=None,
        layer2_json=None,
        layer3_json=None,
    )
    with pytest.raises(SystemExit):
        validate_cli_args(frozenset({1, 2, 3}), ns, p)


def test_validate_cli_stage1_forbids_manifest(tmp_path: Path) -> None:
    p = _parser()
    m = tmp_path / "timeline_manifest.json"
    m.write_text("{}", encoding="utf-8")
    ns = argparse.Namespace(
        stage="1",
        from_stage=None,
        input=tmp_path / "v.mp4",
        manifest=m,
        layer1_json=None,
        layer2_json=None,
        layer3_json=None,
    )
    with pytest.raises(SystemExit):
        validate_cli_args(frozenset({1}), ns, p)


def test_validate_cli_stage23_requires_manifest(tmp_path: Path) -> None:
    p = _parser()
    ns = argparse.Namespace(
        stage="23",
        from_stage=None,
        input=None,
        manifest=None,
        layer1_json=None,
        layer2_json=None,
        layer3_json=None,
    )
    with pytest.raises(SystemExit):
        validate_cli_args(frozenset({2, 3}), ns, p)


def test_validate_cli_stage23_ok(tmp_path: Path) -> None:
    p = _parser()
    m = tmp_path / "timeline_manifest.json"
    m.write_text("{}", encoding="utf-8")
    ns = argparse.Namespace(
        stage="23",
        from_stage=None,
        input=None,
        manifest=m,
        layer1_json=None,
        layer2_json=None,
        layer3_json=None,
    )
    validate_cli_args(frozenset({2, 3}), ns, p)
