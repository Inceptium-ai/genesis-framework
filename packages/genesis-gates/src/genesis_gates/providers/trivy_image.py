"""``trivy-image`` provider — scan a container image for known vulnerabilities.

Runs ``trivy image --format json <ref>`` against the image configured via
``gates.container-scan.image`` and turns the ``Results[].Vulnerabilities[]``
entries into findings (the same trivy JSON shape the ``dependency-scan`` fs scan
consumes).

Status logic:
  * no ``image`` configured -> ``skipped`` (nothing to scan).
  * ``trivy`` not on PATH -> ``skipped``.
  * scan yields vulnerabilities -> ``failed``; none -> ``passed``.
  * trivy times out / output unparseable -> ``error``.
"""
from __future__ import annotations

import json
import time

from ..engine.base import Finding, GateContext, GateResult, GateStatus, Severity
from ..engine.provider import Provider, register_provider
from ..util import DEFAULT_TIMEOUT, map_severity, run_cmd, which


def _findings_from_trivy(raw: str) -> list[Finding]:
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
                    gate="",
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


@register_provider
class TrivyImageProvider(Provider):
    key = "trivy-image"
    description = "Scan a container image for known vulnerabilities (trivy image)."

    def is_available(self, ctx: GateContext) -> bool:
        return which("trivy") is not None

    def run(self, ctx: GateContext) -> GateResult:
        start = time.perf_counter()
        elapsed = lambda: time.perf_counter() - start  # noqa: E731

        image = self.settings.get("image")
        if not image:
            return GateResult(
                gate="",
                status=GateStatus.skipped,
                summary="no image configured (set gates.container-scan.image)",
                duration_s=elapsed(),
            )
        if not which("trivy"):
            return GateResult(
                gate="",
                status=GateStatus.skipped,
                summary="trivy not installed",
                duration_s=elapsed(),
            )

        timeout = int(self.settings.get("timeout", DEFAULT_TIMEOUT))
        res = run_cmd(
            ["trivy", "image", "--format", "json", str(image)],
            timeout=timeout,
        )
        if res.timed_out:
            return GateResult(
                gate="",
                status=GateStatus.error,
                summary=f"trivy timed out after {timeout}s",
                error="timeout",
                duration_s=elapsed(),
            )

        # trivy exits non-zero when it finds vulnerabilities, so we parse
        # regardless of rc and only error on a genuine parse failure.
        try:
            findings = _findings_from_trivy(res.stdout)
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as e:
            return GateResult(
                gate="",
                status=GateStatus.error,
                summary="could not parse trivy output",
                error=f"{type(e).__name__}: {e}; stderr={res.stderr.strip()[:300]}",
                duration_s=elapsed(),
            )

        if findings:
            worst = min(findings, key=lambda f: list(Severity).index(f.severity)).severity
            return GateResult(
                gate="",
                status=GateStatus.failed,
                summary=f"trivy: {len(findings)} vulnerability finding(s) in {image}, "
                f"worst={worst.value}",
                findings=findings,
                duration_s=elapsed(),
            )
        return GateResult(
            gate="",
            status=GateStatus.passed,
            summary=f"trivy: no known vulnerabilities in {image}",
            duration_s=elapsed(),
        )
