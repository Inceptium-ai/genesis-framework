"""Concrete gate behaviour + registry population (provider model).

Gates are now thin: they declare ``providers`` and inherit the base dispatch.
Real tool availability is decided per-provider inside ``run`` (the base
``is_available`` is optimistic), so a gate with no available provider returns a
``skipped`` result rather than reporting ``is_available() is False``.

Deterministic: the risk-classify LLM path is disabled by forcing the ``claude``
binary to look absent, so the rules provider always runs.
"""
from __future__ import annotations

import genesis_gates.gates  # noqa: F401  (import for gate + provider registration side effects)
from genesis_gates.engine.base import GateContext, GateStatus, Severity
from genesis_gates.engine.provider import PROVIDER_REGISTRY
from genesis_gates.engine.registry import GATE_REGISTRY, get_gate

from .conftest import SAMPLE_REPO

EXPECTED_GATES = {
    "risk-classify",
    "unit-tests",
    "dependency-scan",
    "secret-scan",
    "sast",
    "sbom",
}

EXPECTED_PROVIDERS = {
    "trivy",
    "pip-audit",
    "gitleaks",
    "semgrep",
    "syft",
    "pytest",
    "llm",
    "rules",
}


def test_all_six_gates_registered():
    assert EXPECTED_GATES.issubset(set(GATE_REGISTRY))
    assert len(EXPECTED_GATES) == 6


def test_all_eight_providers_registered():
    assert EXPECTED_PROVIDERS.issubset(set(PROVIDER_REGISTRY))


def _manifest() -> dict:
    return {
        "change": {"id": "CHG-2026-00421", "title": "t", "source": "ai-assisted"},
        "service": {
            "name": "document-api",
            "owner": "customer-platform",
            "criticality": "high",
            "data_classification": "confidential",
        },
        "api": {"openapi_changed": True, "breaking_change": False},
        "release": {"risk_level": "medium"},
    }


def test_risk_classify_uses_rules_fallback_when_claude_absent(monkeypatch):
    # Force the LLM path off regardless of whether `claude` is installed: the
    # `llm` provider self-skips, so the gate falls through to the `rules`
    # provider (always available).
    import genesis_gates.providers.risk_llm as risk_llm
    import genesis_gates.util as util

    monkeypatch.setattr(util, "which", lambda *names: None)
    monkeypatch.setattr(risk_llm, "which", lambda *names: None)

    gate = get_gate("risk-classify")()
    result = gate.run(GateContext(repo_path=".", manifest=_manifest()))

    assert result.status == GateStatus.passed  # advisory: always passes
    assert len(result.findings) == 1
    finding = result.findings[0]
    # Advisory severity is never above low.
    assert finding.severity in (Severity.info, Severity.low)
    assert finding.raw["method"] == "rules"
    assert finding.raw["risk_level"] in ("low", "medium", "high", "critical")


def test_unit_tests_gate_passes_on_sample_repo():
    gate = get_gate("unit-tests")()
    result = gate.run(GateContext(repo_path=str(SAMPLE_REPO), manifest=_manifest()))
    assert result.status == GateStatus.passed, result.summary
    assert "passed" in result.summary


def test_tool_wrapping_gate_skips_when_binary_missing(monkeypatch):
    # secret-scan's only provider wraps gitleaks; with the binary absent no
    # provider is available, so the gate's run() must return `skipped`.
    import genesis_gates.providers.gitleaks as gitleaks
    import genesis_gates.util as util

    monkeypatch.setattr(util, "which", lambda *names: None)
    monkeypatch.setattr(gitleaks, "which", lambda *names: None)

    gate = get_gate("secret-scan")()
    result = gate.run(GateContext(repo_path=str(SAMPLE_REPO), manifest=_manifest()))
    assert result.status == GateStatus.skipped


def test_dependency_scan_skips_without_scanner(monkeypatch):
    # No trivy and no pip-audit -> no available provider -> `skipped`.
    import genesis_gates.providers.pip_audit as pip_audit
    import genesis_gates.providers.trivy as trivy
    import genesis_gates.util as util

    monkeypatch.setattr(util, "which", lambda *names: None)
    monkeypatch.setattr(trivy, "which", lambda *names: None)
    monkeypatch.setattr(pip_audit, "which", lambda *names: None)

    gate = get_gate("dependency-scan")()
    result = gate.run(GateContext(repo_path=str(SAMPLE_REPO), manifest=_manifest()))
    assert result.status == GateStatus.skipped
