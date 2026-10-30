"""semgrep provider — static application security testing via semgrep.

`semgrep --config auto --json <repo>`. semgrep normally exits 0 even when it
reports findings, so results are parsed from the JSON regardless of exit code.
Findings present -> `failed`, none -> `passed`, semgrep absent -> `skipped`,
scan/parse error -> `error`.

Settings (`.genesis/config.yaml` → `gates.sast`):

- ``timeout`` — seconds (default 120).
- ``skip_dirs`` — repo-relative directories to leave out of the scan, one
  ``--exclude`` flag each (see `genesis_gates.util.configured_skip_dirs`).
  A bare semgrep ``--exclude foo`` matches *any* path segment named ``foo``
  anywhere in the tree; the leading ``./`` this provider adds anchors the
  pattern at the scan root, so ``vendor`` excludes ``<repo>/vendor`` and not
  ``<repo>/packages/x/vendor`` — the same repo-relative meaning trivy's
  ``--skip-dirs`` has. Nested trees are listed explicitly (``packages/x/vendor``).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..engine.base import Finding, GateContext, GateResult, GateStatus, Severity
from ..engine.provider import Provider, register_provider
from ..util import configured_skip_dirs, map_severity, run_cmd, which


def _findings_from_semgrep(gate: str, raw: str) -> list[Finding]:
    data = json.loads(raw)
    findings: list[Finding] = []
    for r in data.get("results") or []:
        check = r.get("check_id", "rule")
        path = r.get("path", "")
        line = (r.get("start") or {}).get("line")
        extra = r.get("extra") or {}
        loc = f"{path}:{line}" if path and line is not None else (path or None)
        # semgrep severities: ERROR/WARNING/INFO.
        sev = map_severity(extra.get("severity"), default=Severity.medium)
        findings.append(
            Finding(
                gate=gate,
                title=f"{check.split('.')[-1]}",
                severity=sev,
                detail=(extra.get("message") or check)[:400],
                location=loc,
                remediation=(extra.get("metadata") or {}).get("references", [None])[0]
                if isinstance((extra.get("metadata") or {}).get("references"), list)
                else None,
                raw={"check_id": check, "path": path, "line": line,
                     "severity": extra.get("severity")},
            )
        )
    return findings


@register_provider
class SemgrepProvider(Provider):
    key = "semgrep"
    description = "Static application security testing (semgrep --config auto)."

    def is_available(self, ctx: GateContext) -> bool:
        return which("semgrep") is not None

    def run(self, ctx: GateContext) -> GateResult:
        start = time.perf_counter()
        if not which("semgrep"):
            return GateResult(
                gate=self.key, status=GateStatus.skipped,
                summary="semgrep not installed",
                duration_s=time.perf_counter() - start,
            )

        repo = str(Path(ctx.repo_path))
        timeout = int(self.settings.get("timeout", 120))
        cmd = ["semgrep", "--config", "auto", "--json", "--quiet"]
        # "./" anchors the pattern at the scan root (a bare name would also
        # exclude same-named directories nested anywhere in the tree).
        for skipped in configured_skip_dirs(self.settings):
            cmd += ["--exclude", f"./{skipped}"]
        res = run_cmd([*cmd, repo], timeout=timeout)
        if res.timed_out:
            return GateResult(
                gate=self.key, status=GateStatus.error,
                summary=f"semgrep timed out after {timeout}s", error="timeout",
                duration_s=time.perf_counter() - start,
            )
        try:
            findings = _findings_from_semgrep(self.key, res.stdout)
        except (json.JSONDecodeError, TypeError, AttributeError, KeyError) as e:
            return GateResult(
                gate=self.key, status=GateStatus.error,
                summary="could not parse semgrep output",
                error=f"{type(e).__name__}: {e}; stderr={res.stderr.strip()[:300]}",
                duration_s=time.perf_counter() - start,
            )

        if findings:
            worst = min(findings, key=lambda f: list(Severity).index(f.severity)).severity
            return GateResult(
                gate=self.key, status=GateStatus.failed,
                summary=f"semgrep: {len(findings)} finding(s), worst={worst.value}",
                findings=findings,
                duration_s=time.perf_counter() - start,
            )
        return GateResult(
            gate=self.key, status=GateStatus.passed,
            summary="semgrep: no findings",
            duration_s=time.perf_counter() - start,
        )
