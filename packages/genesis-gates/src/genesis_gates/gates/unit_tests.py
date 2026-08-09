"""unit-tests gate — run the repo's test suite and report pass/fail counts.

Thin gate: delegates to the ``pytest`` built-in provider (or a generic provider
from config). Failing tests -> ``failed``; passing -> ``passed``.
"""
from __future__ import annotations

from ..engine.base import Gate
from ..engine.registry import register_gate


@register_gate
class UnitTestsGate(Gate):
    name = "unit-tests"
    description = "Run pytest in the target repo and report pass/fail counts."
    providers = ["pytest"]
