"""design-lint gate — check UI code against the anti-slop design ban list.

Closes the last gap in the design loop: a project can choose a design, carry
it through every generation prompt, and still ship UI that ignores it — nothing
checked the built code. This gate does, with the same deterministic G-rule ban
list the generation prompts carry (via the ``slop-regex`` provider), plus any
project-specific rules from ``rules_file`` (e.g. a locked design family's
mechanically-checkable guardrails).
"""
from __future__ import annotations

from ..engine.base import Gate
from ..engine.registry import register_gate


@register_gate
class DesignLintGate(Gate):
    name = "design-lint"
    description = "Detect known AI-slop design patterns in UI code (regex ban list)."
    providers = ["slop-regex"]
