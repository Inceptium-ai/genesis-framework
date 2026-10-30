"""trivy provider — find known-vulnerable dependencies with `trivy fs`.

`trivy fs --scanners vuln --format json <repo>`. trivy exits non-zero when it
finds vulnerabilities (expected), so output is parsed regardless of exit code.
Findings present -> `failed`, none -> `passed`, trivy absent -> `skipped`,
timeout / unparseable output -> `error`.

If the scanned repo ships a `.trivyignore.yaml` (preferred: supports per-path
scoping and a required `statement` rationale) or `.trivyignore` at its root, it
is passed via `--ignorefile`. trivy only auto-discovers ignore files relative
to the *current working directory*, not the scan target, so without this a
repo's own documented suppressions would silently not apply.

Settings (`.genesis/config.yaml` → `gates.dependency-scan`):

- ``timeout`` — seconds (default 120).
- ``skip_dirs`` — repo-relative directories to leave out of the scan, one
  ``--skip-dirs`` flag each (see `genesis_gates.util.configured_skip_dirs`).
  trivy anchors these at the scan target, so ``vendor`` skips ``<repo>/vendor``
  and *not* ``<repo>/packages/x/vendor`` — list a nested tree explicitly.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..engine.base import Finding, GateContext, GateResult, GateStatus, Severity
from ..engine.provider import Provider, register_provider
from ..util import configured_skip_dirs, map_severity, run_cmd, which


def _findings_from_trivy(gate: str, raw: str) -> list[Finding]:
    data = json.loads(raw)
    findings: list[Finding] = []
    for result in data.get("Results") or []:
        target = result.get("Target", "")
        for v in result.get("Vulnerabilities") or []:
            vid = v.get("VulnerabilityID", "?")
            pkg = v.get("PkgName", "?")
            installed = v.get("InstalledVersion", "")
            fixed = v.get("FixedVersion")
            findings.append(
                Finding(
                    gate=gate,
                    title=f"{pkg} {installed}: {vid}",
                    severity=map_severity(v.get("Severity")),
                    detail=(v.get("Title") or v.get("Description") or "")[:400],
                    location=target or None,
                    remediation=(f"Upgrade {pkg} to {fixed}" if fixed else None),
                    raw={"id": vid, "package": pkg, "installed": installed,
                         "fixed": fixed, "severity": v.get("Severity")},
                )
            )
    return findings


def repo_ignorefile(repo: Path) -> Path | None:
    """Return the repo's own trivy ignore file, if it ships one."""
    for name in (".trivyignore.yaml", ".trivyignore"):
        candidate = repo / name
        if candidate.is_file():
            return candidate
    return None


@register_provider
class TrivyProvider(Provider):
    key = "trivy"
    description = "Scan dependencies for known vulnerabilities with trivy."

    def is_available(self, ctx: GateContext) -> bool:
        return which("trivy") is not None

    def run(self, ctx: GateContext) -> GateResult:
        start = time.perf_counter()
        if not which("trivy"):
            return GateResult(
                gate=self.key, status=GateStatus.skipped,
                summary="trivy not installed",
                duration_s=time.perf_counter() - start,
            )

        repo = Path(ctx.repo_path)
        timeout = int(self.settings.get("timeout", 120))
        cmd = ["trivy", "fs", "--scanners", "vuln", "--format", "json"]
        ignorefile = repo_ignorefile(repo)
        if ignorefile is not None:
            cmd += ["--ignorefile", str(ignorefile)]
        # Out-of-scope trees (vendored, generated, packaged sample apps). trivy
        # matches --skip-dirs relative to the scan target, which is exactly the
        # repo-relative meaning of the setting, so entries are passed as-is.
        for skipped in configured_skip_dirs(self.settings):
            cmd += ["--skip-dirs", skipped]
        res = run_cmd([*cmd, str(repo)], timeout=timeout)
        if res.timed_out:
            return GateResult(
                gate=self.key, status=GateStatus.error,
                summary=f"trivy timed out after {timeout}s", error="timeout",
                duration_s=time.perf_counter() - start,
            )
        # trivy exits non-zero when vulns are found; parse regardless and only
        # error on a real parse failure.
        try:
            findings = _findings_from_trivy(self.key, res.stdout)
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as e:
            return GateResult(
                gate=self.key, status=GateStatus.error,
                summary="could not parse trivy output",
                error=f"{type(e).__name__}: {e}; stderr={res.stderr.strip()[:300]}",
                duration_s=time.perf_counter() - start,
            )

        if findings:
            worst = min(findings, key=lambda f: list(Severity).index(f.severity)).severity
            return GateResult(
                gate=self.key, status=GateStatus.failed,
                summary=f"trivy: {len(findings)} vulnerable dependency finding(s), "
                f"worst={worst.value}",
                findings=findings,
                duration_s=time.perf_counter() - start,
            )
        return GateResult(
            gate=self.key, status=GateStatus.passed,
            summary="trivy: no known-vulnerable dependencies",
            duration_s=time.perf_counter() - start,
        )
