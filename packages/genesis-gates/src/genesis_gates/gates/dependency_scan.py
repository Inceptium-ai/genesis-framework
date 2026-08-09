"""dependency-scan gate — find known-vulnerable dependencies.

Thin gate: declares WHAT (scan dependencies for known CVEs) and the ordered
built-in providers that can produce it (``trivy`` preferred, ``pip-audit``
fallback). The base ``Gate.run`` resolves a provider — from
``.genesis/config.yaml`` or the first available built-in — and delegates to it.
"""
from __future__ import annotations

from ..engine.base import Gate
from ..engine.registry import register_gate


@register_gate
class DependencyScanGate(Gate):
    name = "dependency-scan"
    description = "Scan dependencies for known vulnerabilities (trivy or pip-audit)."
    providers = ["trivy", "pip-audit"]
