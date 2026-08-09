"""iac-policy-check gate — enforce policy on IaC/config via OPA/conftest.

A thin gate: it declares WHAT (a policy check over infrastructure/config) and
defers the HOW to the ``conftest`` provider (or a generic BYO provider via
config). Self-skips cleanly when no policy/paths are configured or conftest is
unavailable.
"""
from __future__ import annotations

from ..engine.base import Gate
from ..engine.registry import register_gate


@register_gate
class IacPolicyCheckGate(Gate):
    name = "iac-policy-check"
    description = "Enforce policy on IaC/config with OPA/conftest."
    providers = ["conftest"]
