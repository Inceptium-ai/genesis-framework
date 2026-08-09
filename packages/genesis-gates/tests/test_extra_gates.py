"""Tests for the two manifest-vocabulary gates: container-scan + iac-policy-check.

These are deterministic and need no external tools: with empty config both gates
resolve their built-in provider and self-skip (no image / no policy configured),
so they never break a run.
"""
from __future__ import annotations

import genesis_gates.gates  # noqa: F401  (import registers gates + providers)
from genesis_gates.engine.base import GateContext, GateStatus
from genesis_gates.engine.provider import PROVIDER_REGISTRY
from genesis_gates.engine.registry import GATE_REGISTRY


def _ctx() -> GateContext:
    return GateContext(repo_path=".", manifest={}, config={})


def test_gates_registered() -> None:
    assert "container-scan" in GATE_REGISTRY
    assert "iac-policy-check" in GATE_REGISTRY


def test_providers_registered() -> None:
    assert "trivy-image" in PROVIDER_REGISTRY
    assert "conftest" in PROVIDER_REGISTRY


def test_container_scan_skips_without_image() -> None:
    gate = GATE_REGISTRY["container-scan"]()
    result = gate.run(_ctx())
    assert result.gate == "container-scan"
    assert result.status is GateStatus.skipped
    assert not result.findings


def test_iac_policy_check_skips_without_config() -> None:
    gate = GATE_REGISTRY["iac-policy-check"]()
    result = gate.run(_ctx())
    assert result.gate == "iac-policy-check"
    assert result.status is GateStatus.skipped
    assert not result.findings
