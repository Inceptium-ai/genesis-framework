"""``report`` provider — defer-to-CI import.

Runs NOTHING. It reads a scan report your CI already produced (SARIF, JUnit,
CycloneDX, …) and normalizes it into findings. This lets a team keep their
existing pipeline tools while still funneling results through Genesis gates.

Status logic:
  * findings present -> ``failed``.
  * no findings -> ``passed``.
  * EXCEPT ``cyclonedx`` (an SBOM inventory, not a check) -> always ``passed``
    with the info finding (the component count) attached.
  * missing file / unknown or unsupported parser / unparseable -> ``error``.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..engine.base import GateContext, GateResult, GateStatus, Severity
from ..engine.provider import Provider, register_provider
from ..parsers import PARSERS, UNSUPPORTED_PARSERS


@register_provider
class ReportProvider(Provider):
    key = "report"
    description = "Import a scan report your CI already produced (no execution)."

    def run(self, ctx: GateContext) -> GateResult:
        start = time.perf_counter()
        elapsed = lambda: time.perf_counter() - start  # noqa: E731
        report = self.settings.get("report")
        parser = self.settings.get("parser")

        if not report:
            return _error("report provider requires a `report` path setting", start)
        if not parser:
            return _error("report provider requires a `parser` setting", start)
        if parser not in PARSERS:
            return _parser_error(parser, start)

        path = Path(report)
        if not path.is_absolute():
            path = Path(ctx.repo_path) / path
        if not path.is_file():
            return _error(f"report file not found: {path}", start)

        try:
            text = path.read_text()
            findings = PARSERS[parser](text)
        except Exception as e:  # noqa: BLE001 - any read/parse failure is an `error`
            return _error(
                f"could not parse report {path} as {parser}",
                start,
                error=f"{type(e).__name__}: {e}",
            )

        # An SBOM is an inventory, not a pass/fail check: always passed.
        if parser == "cyclonedx":
            return GateResult(
                gate="",
                status=GateStatus.passed,
                summary=findings[0].title if findings else "SBOM imported",
                findings=findings,
                duration_s=elapsed(),
            )

        if findings:
            worst = min(findings, key=lambda f: list(Severity).index(f.severity)).severity
            return GateResult(
                gate="",
                status=GateStatus.failed,
                summary=f"{parser} report: {len(findings)} finding(s), worst={worst.value}",
                findings=findings,
                duration_s=elapsed(),
            )
        return GateResult(
            gate="",
            status=GateStatus.passed,
            summary=f"{parser} report: no findings",
            duration_s=elapsed(),
        )


def _error(summary: str, start: float, error: str | None = None) -> GateResult:
    return GateResult(
        gate="",
        status=GateStatus.error,
        summary=summary,
        error=error or summary,
        duration_s=time.perf_counter() - start,
    )


def _parser_error(parser: Any, start: float) -> GateResult:
    if parser in UNSUPPORTED_PARSERS:
        msg = f"parser {parser!r} not yet supported"
    else:
        msg = f"unknown parser {parser!r} (known: {', '.join(sorted(PARSERS))})"
    return _error(msg, start)
