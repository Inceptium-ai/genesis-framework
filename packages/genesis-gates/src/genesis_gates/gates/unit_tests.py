"""unit-tests gate — run the repo's test suites and report pass/fail counts.

Polyglot by default. Most real repos (and nearly every AI-generated app) are
more than one language: a TypeScript frontend next to a Python backend. Running
a single provider would test half the repo and call the other half "skipped", so
this gate runs *every* built-in runner and aggregates the results into one
``GateResult``:

* ``failed``  — any ecosystem failed (a red suite anywhere blocks the release)
* ``error``   — a runner broke and nothing failed outright
* ``passed``  — at least one suite ran green and nothing failed or errored
* ``skipped`` — no ecosystem had anything to run

The summary names each ecosystem that ran (``"python: 51 passed · node:
12 passed"``); if everything skipped it names each reason instead, so a repo
with no tests says *why* rather than going quietly green-adjacent. An ecosystem
that is present but untested (a React app with no test script) skips *with* a
low-severity finding, and is named too — that blind spot is the reason this gate
went polyglot.

Setting ``provider:`` in ``.genesis/config.yaml`` opts out of the aggregation and
pins the gate to that one provider (including the generic ``command`` /
``report`` / ``external`` providers) — the BYO and defer-to-CI paths are
unchanged.
"""
from __future__ import annotations

from ..engine.base import Gate, GateContext, GateResult, GateStatus
from ..engine.provider import PROVIDER_REGISTRY
from ..engine.registry import register_gate

# Longest an all-skipped summary may get before the tail is elided.
_MAX_SUMMARY = 400


@register_gate
class UnitTestsGate(Gate):
    name = "unit-tests"
    description = "Run the repo's test suites (python, node, go, rust) and report counts."
    providers = ["pytest", "node-test", "go-test", "cargo-test"]

    def run(self, ctx: GateContext) -> GateResult:
        settings = ctx.gate_settings(self.name)
        if settings.get("provider"):
            # Explicit choice: single provider, base dispatch (and its config
            # validation — an unsupported name must still raise).
            return super().run(ctx)

        labelled: list[tuple[str, GateResult]] = []
        for key in self.providers:
            cls = PROVIDER_REGISTRY.get(key)
            if cls is None:  # provider module not installed in this build
                continue
            provider = cls(settings)
            label = getattr(cls, "ecosystem", "") or key
            if not provider.is_available(ctx):
                labelled.append((label, GateResult(
                    gate=self.name, status=GateStatus.skipped,
                    summary=f"{key}: toolchain not available",
                )))
                continue
            labelled.append((label, provider.run(ctx)))

        if not labelled:
            return GateResult(
                gate=self.name, status=GateStatus.skipped,
                summary=f"no test-runner providers registered ({', '.join(self.providers)})",
            )
        return self._apply_policy(self._aggregate(labelled), settings)

    def _aggregate(self, labelled: list[tuple[str, GateResult]]) -> GateResult:
        statuses = {r.status for _, r in labelled}
        if GateStatus.failed in statuses:
            status = GateStatus.failed
        elif GateStatus.error in statuses:
            status = GateStatus.error
        elif GateStatus.passed in statuses:
            status = GateStatus.passed
        else:
            status = GateStatus.skipped

        # Report the ecosystems that ran, plus any that skipped with something to
        # say (a JS project present but untested). If nothing at all ran, report
        # every reason — a silent skip on unit-tests is the failure mode to avoid.
        notable = [
            (label, r) for label, r in labelled
            if r.status is not GateStatus.skipped or r.findings
        ]
        shown = notable or labelled
        summary = " · ".join(f"{label}: {r.summary}" for label, r in shown)
        if len(summary) > _MAX_SUMMARY:
            summary = summary[:_MAX_SUMMARY - 1] + "…"

        findings = []
        for label, result in labelled:
            for finding in result.findings:
                finding.gate = self.name
                finding.raw.setdefault("ecosystem", label)
                if not finding.title.startswith(f"{label}:"):
                    finding.title = f"{label}: {finding.title}"
                findings.append(finding)

        errors = [f"{label}: {r.error}" for label, r in labelled if r.error]
        return GateResult(
            gate=self.name,
            status=status,
            summary=summary,
            findings=findings,
            error=" · ".join(errors) or None,
            duration_s=sum(r.duration_s for _, r in labelled),
        )
