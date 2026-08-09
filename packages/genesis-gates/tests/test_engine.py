"""GateRunner orchestration + EvidencePackage shape."""
from __future__ import annotations

from genesis_gates.contracts import validate_evidence
from genesis_gates.engine.base import (
    Finding,
    Gate,
    GateContext,
    GateResult,
    GateStatus,
    Severity,
)
from genesis_gates.engine.runner import GateRunner


class PassGate(Gate):
    name = "pass-gate"

    def run(self, ctx: GateContext) -> GateResult:
        return GateResult(gate=self.name, status=GateStatus.passed, summary="ok")


class FailGate(Gate):
    name = "fail-gate"

    def run(self, ctx: GateContext) -> GateResult:
        return GateResult(
            gate=self.name,
            status=GateStatus.failed,
            summary="a test failed",
            findings=[
                Finding(gate=self.name, title="boom", severity=Severity.high, detail="d")
            ],
        )


class ErrorGate(Gate):
    name = "error-gate"

    def run(self, ctx: GateContext) -> GateResult:
        raise RuntimeError("gate blew up")


class SkipGate(Gate):
    name = "skip-gate"

    def is_available(self) -> bool:
        return False

    def run(self, ctx: GateContext) -> GateResult:  # pragma: no cover - never called
        raise AssertionError("run() must not be called when is_available() is False")


def _ctx() -> GateContext:
    manifest = {
        "change": {"id": "CHG-2026-00099", "title": "t", "source": "human"},
        "service": {"name": "svc", "owner": "team"},
        "release": {"risk_level": "low"},
    }
    return GateContext(repo_path=".", manifest=manifest)


def _statuses(pkg):
    return {r.gate: r.status for r in pkg.gate_results}


def test_failed_takes_precedence_over_error_and_passed():
    pkg = GateRunner([PassGate(), FailGate(), ErrorGate(), SkipGate()]).run(_ctx())
    assert pkg.status == "failed"


def test_error_wins_when_no_failure():
    pkg = GateRunner([PassGate(), ErrorGate(), SkipGate()]).run(_ctx())
    assert pkg.status == "error"


def test_all_passed_when_only_passing_and_skips():
    pkg = GateRunner([PassGate(), SkipGate()]).run(_ctx())
    assert pkg.status == "passed"


def test_erroring_gate_is_caught_and_recorded():
    pkg = GateRunner([ErrorGate()]).run(_ctx())
    statuses = _statuses(pkg)
    assert statuses["error-gate"] == GateStatus.error
    err = next(r for r in pkg.gate_results if r.gate == "error-gate")
    assert "RuntimeError" in (err.error or "")


def test_unavailable_gate_is_marked_skipped():
    pkg = GateRunner([SkipGate()]).run(_ctx())
    statuses = _statuses(pkg)
    assert statuses["skip-gate"] == GateStatus.skipped


def test_evidence_package_carries_manifest_derived_fields():
    pkg = GateRunner([PassGate()]).run(_ctx())
    assert pkg.change_id == "CHG-2026-00099"
    assert pkg.service == "svc"
    assert pkg.owner == "team"
    assert pkg.risk_level == "low"


def test_evidence_package_to_dict_validates_against_schema():
    pkg = GateRunner([PassGate(), FailGate(), ErrorGate(), SkipGate()]).run(_ctx())
    payload = pkg.to_dict()
    assert validate_evidence(payload) == []
    # Flattened findings union present.
    assert any(f["gate"] == "fail-gate" for f in payload["findings"])
