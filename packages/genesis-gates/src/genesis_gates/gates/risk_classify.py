"""risk-classify gate — assess change risk from the delivery manifest.

Thin gate: delegates to the ``llm`` built-in provider (local ``claude`` CLI,
auto-preferred) and falls back to the always-available ``rules`` provider when
``claude`` is absent. Advisory only: always ``passed`` with one advisory finding.
"""
from __future__ import annotations

from ..engine.base import Gate
from ..engine.registry import register_gate


@register_gate
class RiskClassifyGate(Gate):
    name = "risk-classify"
    description = "Assess change risk from manifest fields (LLM-preferred, rules fallback)."
    providers = ["llm", "rules"]
