"""``external`` provider — attest that another system owns this gate.

Runs NOTHING and produces no findings. It records that a gate is handled outside
Genesis (e.g. a mandatory gitleaks step in the org's central CI), so the gate is
accounted for in the evidence trail without Genesis re-running it. The default
recorded status is ``passed`` (audit-noted); set ``status: skipped`` to record
it as delegated-but-unverified.
"""
from __future__ import annotations

import time

from ..engine.base import GateContext, GateResult, GateStatus
from ..engine.provider import Provider, register_provider


@register_provider
class ExternalProvider(Provider):
    key = "external"
    description = "Record that another system attests this gate (no execution)."

    def run(self, ctx: GateContext) -> GateResult:
        start = time.perf_counter()
        attested_by = self.settings.get("attested_by")
        note = self.settings.get("note")
        status_raw = self.settings.get("status", "passed")

        if not attested_by:
            return GateResult(
                gate="",
                status=GateStatus.error,
                summary="external provider requires an `attested_by` setting",
                error="missing `attested_by`",
                duration_s=time.perf_counter() - start,
            )

        try:
            status = GateStatus(status_raw)
        except ValueError:
            status = GateStatus.passed

        summary = f"attested externally by {attested_by}"
        if note:
            summary = f"{summary} ({note})"

        return GateResult(
            gate="",
            status=status,
            summary=summary,
            findings=[],
            duration_s=time.perf_counter() - start,
        )
