"""syft provider — generate a Software Bill of Materials with syft.

`syft <repo> -o json` (Syft's native JSON) with a cyclonedx-json fallback shape.
Emits one info Finding recording the component count. Status is always `passed`
when syft runs; `skipped` if syft is absent; `error` on a genuine crash.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..engine.base import Finding, GateContext, GateResult, GateStatus, Severity
from ..engine.provider import Provider, register_provider
from ..util import run_cmd, which


def _component_count(raw: str) -> tuple[int, str]:
    """Return (count, format_label). Handles Syft-native JSON ('artifacts') and
    CycloneDX JSON ('components')."""
    data = json.loads(raw)
    if isinstance(data, dict):
        if isinstance(data.get("artifacts"), list):
            return len(data["artifacts"]), "syft-json"
        if isinstance(data.get("components"), list):
            return len(data["components"]), "cyclonedx-json"
    return 0, "unknown"


@register_provider
class SyftProvider(Provider):
    key = "syft"
    description = "Generate a Software Bill of Materials (syft)."

    def is_available(self, ctx: GateContext) -> bool:
        return which("syft") is not None

    def run(self, ctx: GateContext) -> GateResult:
        start = time.perf_counter()
        if not which("syft"):
            return GateResult(
                gate=self.key, status=GateStatus.skipped,
                summary="syft not installed",
                duration_s=time.perf_counter() - start,
            )

        repo = str(Path(ctx.repo_path))
        out_format = str(self.settings.get("format", "json"))
        timeout = int(self.settings.get("timeout", 120))
        res = run_cmd(["syft", repo, "-o", out_format], timeout=timeout)

        if res.timed_out:
            return GateResult(
                gate=self.key, status=GateStatus.error,
                summary=f"syft timed out after {timeout}s", error="timeout",
                duration_s=time.perf_counter() - start,
            )
        if not res.ok or (res.rc != 0 and not res.stdout.strip()):
            return GateResult(
                gate=self.key, status=GateStatus.error,
                summary=f"syft exited {res.rc}",
                error=res.stderr.strip()[:500] or "unknown syft error",
                duration_s=time.perf_counter() - start,
            )
        try:
            count, fmt = _component_count(res.stdout)
        except (json.JSONDecodeError, TypeError) as e:
            return GateResult(
                gate=self.key, status=GateStatus.error,
                summary="could not parse syft output",
                error=f"{type(e).__name__}: {e}",
                duration_s=time.perf_counter() - start,
            )

        finding = Finding(
            gate=self.key,
            title=f"SBOM generated: {count} component(s)",
            severity=Severity.info,
            detail=f"syft catalogued {count} component(s) ({fmt} format).",
            location=repo,
            raw={"component_count": count, "format": fmt},
        )
        return GateResult(
            gate=self.key, status=GateStatus.passed,
            summary=f"{count} component(s) catalogued",
            findings=[finding],
            duration_s=time.perf_counter() - start,
        )
