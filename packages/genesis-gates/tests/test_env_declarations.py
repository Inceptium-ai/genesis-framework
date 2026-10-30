"""`declared_env_vars` — the repo's own env files as its declared contract.

A brownfield audit has no stored dependency map, so without this every compose
``${VAR}`` without a default counted as an undeclared gap even when the repo
documents it in `.env.example`: the env_hygiene category measured "does this
machine hold values" instead of "are env vars declared".
"""
from __future__ import annotations

from pathlib import Path

import pytest
from genesis_gates.analysis import declared_env_vars, reconcile


def test_reads_env_example_family_but_not_comments(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text(
        "# Genesis environment\n"
        "GENESIS_ENCRYPTION_KEY=\n"
        "export EXPORTED_VAR=1\n"
        "  SPACED_VAR = value\n"
        "# COMMENTED_OUT=not-declared\n"
        "lowercase_ignored=1\n"
    )
    (tmp_path / ".env.prod.example").write_text("STRIPE_SECRET_KEY=\n")
    (tmp_path / ".env.sample").write_text("SAMPLE_VAR=\n")
    declared = declared_env_vars(tmp_path)
    assert set(declared) == {
        "GENESIS_ENCRYPTION_KEY", "EXPORTED_VAR", "SPACED_VAR",
        "STRIPE_SECRET_KEY", "SAMPLE_VAR",
    }
    assert "COMMENTED_OUT" not in declared
    # Each entry records which file declared it (audit trail in the report).
    assert ".env.prod.example" in declared["STRIPE_SECRET_KEY"]["source_detected"]


def test_reads_local_env_when_present_without_touching_values(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LOCAL_ONLY=super-secret-value\n")
    declared = declared_env_vars(tmp_path)
    assert set(declared) == {"LOCAL_ONLY"}
    # Only the key name is kept — a value must never reach a report.
    assert "super-secret-value" not in repr(declared)


def test_no_env_files_declares_nothing(tmp_path: Path) -> None:
    assert declared_env_vars(tmp_path) == {}


@pytest.mark.parametrize("declared_in", [".env.example", ".env"])
def test_declared_var_is_not_a_missing_required_gap(
    tmp_path: Path, declared_in: str
) -> None:
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  app:\n    environment:\n"
        "      DOCUMENTED: ${DOCUMENTED}\n"
        "      UNDOCUMENTED: ${UNDOCUMENTED}\n"
    )
    (tmp_path / declared_in).write_text("DOCUMENTED=\n")
    recon = reconcile(tmp_path, stored_deps=declared_env_vars(tmp_path))
    assert [d["env_var"] for d in recon["missing_required"]] == ["UNDOCUMENTED"]
