"""sbom gate — generate a Software Bill of Materials.

Thin gate: delegates to the ``syft`` built-in provider (or a generic provider
from config). Advisory: always ``passed`` when the provider runs.
"""
from __future__ import annotations

from ..engine.base import Gate
from ..engine.registry import register_gate


@register_gate
class SbomGate(Gate):
    name = "sbom"
    description = "Generate a Software Bill of Materials (syft)."
    providers = ["syft"]
