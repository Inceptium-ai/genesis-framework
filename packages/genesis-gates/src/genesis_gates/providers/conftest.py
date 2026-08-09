"""``conftest`` provider — OPA/conftest policy checks against IaC/config.

Runs ``conftest test <paths...> --policy <policy> --output json`` and turns each
result's ``failures[]`` (-> high) and ``warnings[]`` (-> medium) into findings.

Status logic:
  * neither ``policy`` nor ``paths`` configured -> ``skipped``.
  * ``conftest`` (or ``opa``) not on PATH -> ``skipped``.
  * policy failures/warnings present -> ``failed``; none -> ``passed``.
  * conftest times out / output unparseable -> ``error``.
"""
from __future__ import annotations

import json
import time

from ..engine.base import Finding, GateContext, GateResult, GateStatus, Severity
from ..engine.provider import Provider, register_provider
from ..util import DEFAULT_TIMEOUT, run_cmd, which


def _as_list(value: object) -> list[str]:
    """Normalise a ``paths`` setting (str or list) into a list of strings."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _findings_from_conftest(raw: str) -> list[Finding]:
    data = json.loads(raw)
    # conftest --output json emits an array of per-file result objects.
    results = data if isinstance(data, list) else [data]
    findings: list[Finding] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        filename = r.get("filename") or r.get("Filename") or ""
        for kind, severity in (("failures", Severity.high), ("warnings", Severity.medium)):
            for item in r.get(kind) or []:
                msg = item.get("msg") if isinstance(item, dict) else str(item)
                meta = item.get("metadata") if isinstance(item, dict) else None
                findings.append(
                    Finding(
                        gate="",
                        title=(msg or kind[:-1])[:200],
                        severity=severity,
                        detail=(msg or "")[:400],
                        location=filename or None,
                        raw={"kind": kind[:-1], "filename": filename,
                             "msg": msg, "metadata": meta},
                    )
                )
    return findings


@register_provider
class ConftestProvider(Provider):
    key = "conftest"
    description = "Run OPA/conftest policy checks against IaC/config files."

    def is_available(self, ctx: GateContext) -> bool:
        return which("conftest", "opa") is not None

    def run(self, ctx: GateContext) -> GateResult:
        start = time.perf_counter()
        elapsed = lambda: time.perf_counter() - start  # noqa: E731

        policy = self.settings.get("policy")
        paths = _as_list(self.settings.get("paths"))
        if not policy and not paths:
            return GateResult(
                gate="",
                status=GateStatus.skipped,
                summary="no policy/paths configured (set gates.iac-policy-check.policy and .paths)",
                duration_s=elapsed(),
            )
        if not which("conftest", "opa"):
            return GateResult(
                gate="",
                status=GateStatus.skipped,
                summary="conftest not installed",
                duration_s=elapsed(),
            )

        timeout = int(self.settings.get("timeout", DEFAULT_TIMEOUT))
        cmd = ["conftest", "test", *paths, "--output", "json"]
        if policy:
            cmd += ["--policy", str(policy)]
        res = run_cmd(cmd, cwd=ctx.repo_path, timeout=timeout)
        if res.timed_out:
            return GateResult(
                gate="",
                status=GateStatus.error,
                summary=f"conftest timed out after {timeout}s",
                error="timeout",
                duration_s=elapsed(),
            )

        # conftest exits non-zero when a policy fails, so parse regardless of rc.
        try:
            findings = _findings_from_conftest(res.stdout)
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as e:
            return GateResult(
                gate="",
                status=GateStatus.error,
                summary="could not parse conftest output",
                error=f"{type(e).__name__}: {e}; stderr={res.stderr.strip()[:300]}",
                duration_s=elapsed(),
            )

        if findings:
            worst = min(findings, key=lambda f: list(Severity).index(f.severity)).severity
            return GateResult(
                gate="",
                status=GateStatus.failed,
                summary=f"conftest: {len(findings)} policy violation(s), worst={worst.value}",
                findings=findings,
                duration_s=elapsed(),
            )
        return GateResult(
            gate="",
            status=GateStatus.passed,
            summary="conftest: no policy violations",
            duration_s=elapsed(),
        )
