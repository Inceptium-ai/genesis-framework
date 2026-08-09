"""pip-audit provider — audit a requirements.txt for known-vulnerable packages.

`pip-audit -f json -r <repo>/requirements.txt`. pip-audit exits non-zero when it
finds vulnerabilities (expected), so output is parsed regardless of exit code.
Findings present -> `failed`, none -> `passed`, pip-audit absent (or no
requirements.txt) -> `skipped`, timeout / unparseable output -> `error`.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..engine.base import Finding, GateContext, GateResult, GateStatus, Severity
from ..engine.provider import Provider, register_provider
from ..util import map_severity, run_cmd, which


def _findings_from_pip_audit(gate: str, raw: str) -> list[Finding]:
    data = json.loads(raw)
    # pip-audit -f json is either {"dependencies": [...]} (newer) or a bare list.
    deps = data.get("dependencies", data) if isinstance(data, dict) else data
    findings: list[Finding] = []
    for dep in deps or []:
        name = dep.get("name", "?")
        version = dep.get("version", "")
        for v in dep.get("vulns") or []:
            vid = v.get("id", "?")
            fixes = v.get("fix_versions") or []
            # pip-audit does not emit a severity; default to medium.
            findings.append(
                Finding(
                    gate=gate,
                    title=f"{name} {version}: {vid}",
                    severity=map_severity(v.get("severity"), default=Severity.medium),
                    detail=(v.get("description") or "")[:400],
                    location="requirements.txt",
                    remediation=(f"Upgrade {name} to {', '.join(fixes)}" if fixes else None),
                    raw={"id": vid, "package": name, "installed": version,
                         "fix_versions": fixes},
                )
            )
    return findings


@register_provider
class PipAuditProvider(Provider):
    key = "pip-audit"
    description = "Audit a requirements.txt for known-vulnerable packages (pip-audit)."

    def is_available(self, ctx: GateContext) -> bool:
        return which("pip-audit") is not None

    def run(self, ctx: GateContext) -> GateResult:
        start = time.perf_counter()
        repo = Path(ctx.repo_path)
        requirements = repo / "requirements.txt"

        if not which("pip-audit"):
            return GateResult(
                gate=self.key, status=GateStatus.skipped,
                summary="pip-audit not installed",
                duration_s=time.perf_counter() - start,
            )
        if not requirements.is_file():
            return GateResult(
                gate=self.key, status=GateStatus.skipped,
                summary="pip-audit: no requirements.txt to audit",
                duration_s=time.perf_counter() - start,
            )

        timeout = int(self.settings.get("timeout", 120))
        res = run_cmd(
            ["pip-audit", "-f", "json", "-r", str(requirements)],
            timeout=timeout,
        )
        if res.timed_out:
            return GateResult(
                gate=self.key, status=GateStatus.error,
                summary=f"pip-audit timed out after {timeout}s", error="timeout",
                duration_s=time.perf_counter() - start,
            )
        try:
            findings = _findings_from_pip_audit(self.key, res.stdout)
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as e:
            return GateResult(
                gate=self.key, status=GateStatus.error,
                summary="could not parse pip-audit output",
                error=f"{type(e).__name__}: {e}; stderr={res.stderr.strip()[:300]}",
                duration_s=time.perf_counter() - start,
            )

        if findings:
            worst = min(findings, key=lambda f: list(Severity).index(f.severity)).severity
            return GateResult(
                gate=self.key, status=GateStatus.failed,
                summary=f"pip-audit: {len(findings)} vulnerable dependency finding(s), "
                f"worst={worst.value}",
                findings=findings,
                duration_s=time.perf_counter() - start,
            )
        return GateResult(
            gate=self.key, status=GateStatus.passed,
            summary="pip-audit: no known-vulnerable dependencies",
            duration_s=time.perf_counter() - start,
        )
