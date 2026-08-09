"""rules provider — deterministic change-risk scorer from manifest fields.

Advisory only: always `passed`, emitting one info/low Finding that records the
assessed risk band + rationale. This is the always-available fallback for the
`risk-classify` gate (the LLM provider defers to these helpers when it cannot
produce a verdict). Available regardless of installed tooling.
"""
from __future__ import annotations

import time

from ..engine.base import Finding, GateContext, GateResult, GateStatus, Severity
from ..engine.provider import Provider, register_provider

_LEVELS = ("low", "medium", "high", "critical")
_LEVEL_SCORE = {"low": 1, "medium": 2, "high": 3, "critical": 4}
_CLASS_SCORE = {"public": 1, "internal": 2, "confidential": 3, "restricted": 4}


def _manifest_fields(manifest: dict) -> dict:
    service = manifest.get("service") or {}
    api = manifest.get("api") or {}
    release = manifest.get("release") or {}
    change = manifest.get("change") or {}
    return {
        "criticality": service.get("criticality"),
        "data_classification": service.get("data_classification"),
        "breaking_change": bool(api.get("breaking_change", False)),
        "openapi_changed": bool(api.get("openapi_changed", False)),
        "declared_risk_level": release.get("risk_level"),
        "source": change.get("source"),
        "service_name": service.get("name"),
    }


def _rules_assess(f: dict) -> tuple[str, str, list[str]]:
    """Deterministic scorer. Returns (risk_level, rationale, key_factors)."""
    factors: list[str] = []
    crit = _LEVEL_SCORE.get(str(f["criticality"]), 1)
    datac = _CLASS_SCORE.get(str(f["data_classification"]), 1)
    declared = _LEVEL_SCORE.get(str(f["declared_risk_level"]), 1)

    # The score is dominated by the most severe single signal, nudged upward by
    # the others so that e.g. a breaking change on a critical service escalates.
    score = max(crit, datac, declared)
    if f["breaking_change"]:
        score += 1
        factors.append("breaking API change")
    if f["source"] in ("ai-generated", "ai-assisted"):
        # AI-authored changes carry marginally more review risk.
        score += 0.5
        factors.append(f"{f['source']} authorship")
    if crit >= 3:
        factors.append(f"{f['criticality']} service criticality")
    if datac >= 3:
        factors.append(f"{f['data_classification']} data classification")
    if declared >= 3:
        factors.append(f"declared {f['declared_risk_level']} release risk")

    if score >= 4.5:
        level = "critical"
    elif score >= 3.5:
        level = "high"
    elif score >= 2.5:
        level = "medium"
    else:
        level = "low"

    if not factors:
        factors.append("all risk signals low")
    rationale = (
        f"Rules scorer: criticality={f['criticality']}, "
        f"data={f['data_classification']}, breaking={f['breaking_change']}, "
        f"declared={f['declared_risk_level']} -> {level} (score {score:g})."
    )
    return level, rationale, factors


def build_result(key: str, f: dict, assessment: dict, method: str, start: float) -> GateResult:
    """Build the advisory `passed` GateResult + single Finding shared by the LLM
    and rules risk providers."""
    level = assessment["risk_level"]
    factors = assessment["key_factors"]
    # Advisory severity: never above low, per the gate contract.
    sev = Severity.low if level in ("high", "critical") else Severity.info

    detail_lines = [
        f"Assessed risk: {level.upper()} (via {method}).",
        assessment["rationale"],
    ]
    if factors:
        detail_lines.append("Key factors: " + "; ".join(factors))
    detail = "\n".join(detail_lines)

    finding = Finding(
        gate=key,
        title=f"Change risk assessed as {level}",
        severity=sev,
        detail=detail,
        location=f.get("service_name"),
        remediation=(
            "Ensure release strategy and required gates match a "
            f"{level}-risk change." if level in ("high", "critical") else None
        ),
        raw={
            "method": method,
            "risk_level": level,
            "key_factors": factors,
            "inputs": f,
        },
    )
    return GateResult(
        gate=key,
        status=GateStatus.passed,  # advisory: always passes
        summary=f"risk={level} ({method})",
        findings=[finding],
        duration_s=time.perf_counter() - start,
    )


@register_provider
class RulesProvider(Provider):
    key = "rules"
    description = "Deterministic manifest-based change-risk scorer (always available)."

    def is_available(self, ctx: GateContext) -> bool:
        return True

    def run(self, ctx: GateContext) -> GateResult:
        start = time.perf_counter()
        f = _manifest_fields(ctx.manifest)
        level, rationale, factors = _rules_assess(f)
        assessment = {"risk_level": level, "rationale": rationale, "key_factors": factors}
        return build_result(self.key, f, assessment, "rules", start)
