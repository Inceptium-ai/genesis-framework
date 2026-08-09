"""gitleaks provider — detect hardcoded secrets in the working tree.

`gitleaks detect --no-git --source <repo>`. gitleaks exits non-zero (1) when it
finds leaks, which is expected: findings -> `failed`, none -> `passed`, gitleaks
absent -> `skipped`, real crash -> `error`.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from ..engine.base import Finding, GateContext, GateResult, GateStatus, Severity
from ..engine.provider import Provider, register_provider
from ..util import run_cmd, which


def _findings_from_report(gate: str, raw: str) -> list[Finding]:
    data = json.loads(raw) if raw.strip() else []
    findings: list[Finding] = []
    for item in data or []:
        rule = item.get("RuleID") or item.get("Rule") or "secret"
        file = item.get("File", "")
        line = item.get("StartLine")
        loc = f"{file}:{line}" if file and line is not None else (file or None)
        secret = item.get("Secret", "")
        redacted = (secret[:4] + "…") if secret else ""
        findings.append(
            Finding(
                gate=gate,
                title=f"Secret detected: {rule}",
                severity=Severity.high,
                detail=(item.get("Description") or rule)
                + (f" (match: {redacted})" if redacted else ""),
                location=loc,
                remediation="Remove the secret, rotate the credential, "
                "and load it from a secret store.",
                raw={"rule": rule, "file": file, "line": line},
            )
        )
    return findings


@register_provider
class GitleaksProvider(Provider):
    key = "gitleaks"
    description = "Detect hardcoded secrets in the working tree (gitleaks)."

    def is_available(self, ctx: GateContext) -> bool:
        return which("gitleaks") is not None

    def run(self, ctx: GateContext) -> GateResult:
        start = time.perf_counter()
        if not which("gitleaks"):
            return GateResult(
                gate=self.key, status=GateStatus.skipped,
                summary="gitleaks not installed",
                duration_s=time.perf_counter() - start,
            )

        repo = str(Path(ctx.repo_path))
        timeout = int(self.settings.get("timeout", 120))
        fd, report = tempfile.mkstemp(suffix=".json", prefix="gitleaks-")
        os.close(fd)
        try:
            res = run_cmd(
                ["gitleaks", "detect", "--no-git", "--source", repo,
                 "--report-format", "json", "--report-path", report],
                timeout=timeout,
            )
            if res.timed_out:
                return GateResult(
                    gate=self.key, status=GateStatus.error,
                    summary=f"gitleaks timed out after {timeout}s", error="timeout",
                    duration_s=time.perf_counter() - start,
                )
            try:
                raw = Path(report).read_text() if os.path.getsize(report) else "[]"
            except OSError:
                raw = "[]"
            try:
                findings = _findings_from_report(self.key, raw)
            except (json.JSONDecodeError, TypeError, AttributeError) as e:
                return GateResult(
                    gate=self.key, status=GateStatus.error,
                    summary="could not parse gitleaks report",
                    error=f"{type(e).__name__}: {e}; stderr={res.stderr.strip()[:300]}",
                    duration_s=time.perf_counter() - start,
                )
        finally:
            try:
                os.unlink(report)
            except OSError:
                pass

        # gitleaks rc: 0 = no leaks, 1 = leaks found (expected). Any other rc
        # with no parsed findings is a genuine failure.
        if not findings and res.rc not in (0, 1):
            return GateResult(
                gate=self.key, status=GateStatus.error,
                summary=f"gitleaks exited {res.rc}",
                error=res.stderr.strip()[:500] or "unknown gitleaks error",
                duration_s=time.perf_counter() - start,
            )

        if findings:
            return GateResult(
                gate=self.key, status=GateStatus.failed,
                summary=f"{len(findings)} secret(s) detected",
                findings=findings,
                duration_s=time.perf_counter() - start,
            )
        return GateResult(
            gate=self.key, status=GateStatus.passed,
            summary="no secrets detected",
            duration_s=time.perf_counter() - start,
        )
