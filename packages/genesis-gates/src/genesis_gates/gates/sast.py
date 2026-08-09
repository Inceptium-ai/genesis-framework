"""sast gate — static application security testing.

Thin gate: delegates to the ``semgrep`` built-in provider (or a generic provider
from config). The base ``Gate.run`` resolves and dispatches; findings ->
``failed``, none -> ``passed``.
"""
from __future__ import annotations

from ..engine.base import Gate
from ..engine.registry import register_gate


@register_gate
class SastGate(Gate):
    name = "sast"
    description = "Static application security testing (semgrep --config auto)."
    providers = ["semgrep"]
