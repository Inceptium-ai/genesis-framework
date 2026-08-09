"""pytest provider builds an isolated env from the app's requirements (GATE-1)."""

from __future__ import annotations

from pathlib import Path

from genesis_gates.engine.base import GateContext, GateStatus
from genesis_gates.providers.pytest_provider import (
    PytestProvider,
    _find_requirements,
)


def test_find_requirements_prefers_backend(tmp_path: Path):
    assert _find_requirements(tmp_path) is None
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "requirements.txt").write_text("fastapi\n")
    found = _find_requirements(tmp_path)
    assert found == tmp_path / "backend" / "requirements.txt"


def test_root_requirements_win_over_backend(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("x\n")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "requirements.txt").write_text("y\n")
    assert _find_requirements(tmp_path) == tmp_path / "requirements.txt"


def test_uninstallable_requirements_skip_not_fail(tmp_path: Path):
    """A dependency install that can't succeed must be reported as skipped —
    never as a false test failure."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_ok():\n    assert True\n")
    # A requirement that cannot resolve makes the venv install fail.
    (tmp_path / "requirements.txt").write_text(
        "genesis-gates-nonexistent-pkg-zzz==9.9.9\n"
    )
    prov = PytestProvider()
    prov.settings = {"timeout": 60}
    res = prov.run(GateContext(repo_path=tmp_path, manifest={}, config={}))
    assert res.status == GateStatus.skipped
    assert "could not prepare test env" in res.summary
