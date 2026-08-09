"""`genesis --version`: reports the installed distribution version, and degrades
gracefully (rather than raising) when the distribution metadata is absent.

The version is single-sourced from `pyproject.toml` via package metadata, so the
test asserts the *shape* and the metadata linkage — never a hardcoded literal
that would have to be bumped alongside every release.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version

import pytest
from genesis_cli import main as main_mod
from genesis_cli.main import main


def test_version_flag_prints_prog_and_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0

    out = capsys.readouterr().out.strip()
    assert out.startswith("genesis ")
    assert re.match(r"^genesis \d+\.\d+", out), out


def test_version_matches_installed_distribution_metadata():
    assert main_mod._version() == version("genesis-cli")


def test_version_falls_back_when_not_installed(monkeypatch):
    """No installed distribution (e.g. a bare source checkout) must not crash."""

    def _missing(_name: str) -> str:
        raise PackageNotFoundError(_name)

    monkeypatch.setattr("importlib.metadata.version", _missing)
    assert "not installed" in main_mod._version()


def test_gates_version_is_single_sourced():
    import genesis_gates

    assert genesis_gates.__version__ == version("genesis-gates")
