"""GateRunner: run a set of gates over a repo+manifest and assemble an EvidencePackage.

The EvidencePackage shape conforms to contracts/evidence.schema.json.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .base import Gate, GateContext, GateResult, GateStatus


@dataclass
class EvidencePackage:
    change_id: str
    service: str | None
    owner: str | None
    risk_level: str | None
    status: str  # passed | failed | error
    created_at: str
    manifest: dict[str, Any]
    gate_results: list[GateResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        findings = [f.to_dict() for r in self.gate_results for f in r.findings]
        return {
            "apiVersion": "genesis.dev/v0",
            "kind": "EvidencePackage",
            "change_id": self.change_id,
            "service": self.service,
            "owner": self.owner,
            "risk_level": self.risk_level,
            "status": self.status,
            "created_at": self.created_at,
            "manifest": self.manifest,
            "gate_results": [r.to_dict() for r in self.gate_results],
            "findings": findings,
        }


class GateRunner:
    def __init__(self, gates: list[Gate]) -> None:
        self.gates = gates

    def run(self, ctx: GateContext) -> EvidencePackage:
        results: list[GateResult] = []
        for gate in self.gates:
            if not gate.is_available():
                results.append(
                    GateResult(
                        gate=gate.name,
                        status=GateStatus.skipped,
                        summary="tool/credential not available",
                    )
                )
                continue
            start = time.monotonic()
            try:
                result = gate.run(ctx)
                result.duration_s = time.monotonic() - start
            except Exception as exc:  # a crashing gate must not sink the run
                result = GateResult(
                    gate=gate.name,
                    status=GateStatus.error,
                    error=f"{type(exc).__name__}: {exc}",
                    duration_s=time.monotonic() - start,
                )
            results.append(result)

        status = self._overall_status(results)
        change = ctx.manifest.get("change", {})
        service = ctx.manifest.get("service", {})
        release = ctx.manifest.get("release", {})
        return EvidencePackage(
            change_id=change.get("id", "UNKNOWN"),
            service=service.get("name"),
            owner=service.get("owner"),
            risk_level=release.get("risk_level"),
            status=status,
            created_at=datetime.now(UTC).isoformat(),
            manifest=ctx.manifest,
            gate_results=results,
        )

    @staticmethod
    def _overall_status(results: list[GateResult]) -> str:
        statuses = {r.status for r in results}
        if GateStatus.failed in statuses:
            return "failed"
        if GateStatus.error in statuses:
            return "error"
        return "passed"
