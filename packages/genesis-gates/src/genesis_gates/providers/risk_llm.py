"""llm provider — assess change risk via the local `claude` CLI.

Advisory only: always `passed`, emitting one info/low Finding recording the
assessed risk band + rationale. Prefers an LLM assessment via the local `claude`
CLI (Claude subscription, no API credits); if `claude` is absent the provider
self-skips (so the gate falls through to the `rules` provider), and if the CLI
call fails or returns junk it defers to the deterministic rules scorer so the
gate still yields its advisory finding.

The claude subprocess + JSON-envelope-unwrap pattern mirrors
webapp/backend/app/aggregator/classify.py::run_claude.
"""
from __future__ import annotations

import json
import re
import time

from ..engine.base import GateContext, GateResult, GateStatus
from ..engine.provider import Provider, register_provider
from ..util import run_cmd, which
from .risk_rules import _LEVELS, _manifest_fields, _rules_assess, build_result

_SYSTEM_PROMPT = (
    "You are a software delivery risk assessor for an AI-native change-governance "
    "control plane. Given the structured fields of a delivery manifest, judge the "
    "overall release risk of the change. Weigh service criticality, data "
    "classification (blast radius / regulatory exposure), whether the API change is "
    "breaking, and the declared release risk level. Respond with ONLY a JSON object "
    '(no prose, no code fences) of the form '
    '{"risk_level":"low|medium|high|critical","rationale":"<=40 words",'
    '"key_factors":["short factor", ...]}.'
)


def _parse_llm_json(raw: str) -> dict | None:
    """Unwrap the `claude --output-format json` envelope, then find the inner
    JSON object the model was asked to emit."""
    if not raw.strip():
        return None
    inner = raw
    try:
        env = json.loads(raw)
        if isinstance(env, dict) and "result" in env:
            inner = env["result"]
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", inner, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    level = str(obj.get("risk_level", "")).strip().lower()
    if level not in _LEVELS:
        return None
    factors = obj.get("key_factors") or []
    if not isinstance(factors, list):
        factors = [str(factors)]
    return {
        "risk_level": level,
        "rationale": str(obj.get("rationale", "")).strip()[:400] or "(no rationale)",
        "key_factors": [str(x)[:80] for x in factors][:8],
    }


def _llm_assess(f: dict, model: str, timeout: int) -> dict | None:
    prompt = (
        "Assess the release risk of this change.\n"
        f"service.name: {f['service_name']}\n"
        f"service.criticality: {f['criticality']}\n"
        f"service.data_classification: {f['data_classification']}\n"
        f"api.breaking_change: {f['breaking_change']}\n"
        f"api.openapi_changed: {f['openapi_changed']}\n"
        f"release.risk_level (declared): {f['declared_risk_level']}\n"
        f"change.source: {f['source']}\n"
    )
    res = run_cmd(
        ["claude", "-p", "--output-format", "json", "--model", model,
         "--system-prompt", _SYSTEM_PROMPT],
        input_text=prompt,
        timeout=timeout,
    )
    if not res.ok or res.rc != 0:
        return None
    return _parse_llm_json(res.stdout)


@register_provider
class LlmProvider(Provider):
    key = "llm"
    description = "Assess change risk via the local claude CLI (rules fallback)."

    def is_available(self, ctx: GateContext) -> bool:
        return which("claude") is not None

    def run(self, ctx: GateContext) -> GateResult:
        start = time.perf_counter()
        if not which("claude"):
            return GateResult(
                gate=self.key, status=GateStatus.skipped,
                summary="claude CLI not installed",
                duration_s=time.perf_counter() - start,
            )

        f = _manifest_fields(ctx.manifest)
        model = str(self.settings.get("risk_model", "sonnet"))
        timeout = int(self.settings.get("timeout", 120))

        assessment = _llm_assess(f, model, timeout)
        if assessment is not None:
            return build_result(self.key, f, assessment, "claude-cli", start)

        # LLM unavailable/junk: defer to the deterministic scorer so the gate
        # still yields its advisory finding.
        level, rationale, factors = _rules_assess(f)
        assessment = {"risk_level": level, "rationale": rationale, "key_factors": factors}
        return build_result(self.key, f, assessment, "rules", start)
