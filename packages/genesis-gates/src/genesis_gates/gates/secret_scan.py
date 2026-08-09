"""secret-scan gate — detect hardcoded secrets in the working tree.

Thin gate: delegates to the ``gitleaks`` built-in provider (or a generic
``command``/``report``/``external`` provider from config). The base ``Gate.run``
resolves and dispatches; findings -> ``failed``, none -> ``passed``.
"""
from __future__ import annotations

from ..engine.base import Gate
from ..engine.registry import register_gate


@register_gate
class SecretScanGate(Gate):
    name = "secret-scan"
    description = "Detect hardcoded secrets in the working tree (gitleaks)."
    providers = ["gitleaks"]
