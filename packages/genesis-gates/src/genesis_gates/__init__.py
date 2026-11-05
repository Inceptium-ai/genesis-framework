"""Genesis gate engine (vendored from Lucidium's ``genesis-toolkit``).

Turns a Delivery Manifest + a repo into a linked, exportable Evidence Package by
running a set of gates. Importing :mod:`genesis_gates.gates` and
:mod:`genesis_gates.providers` populates the gate/provider registries via import
side-effects (the ``@register_gate`` / ``@register_provider`` decorators) and
then loads third-party gates/providers from their entry-point groups; anything
that failed to load is reported in :data:`plugin_errors` (see
:mod:`genesis_gates._plugins`).
"""
from __future__ import annotations

from ._plugins import PluginError, plugin_errors
from .config import load_gate_config
from .engine.base import (
    Finding,
    Gate,
    GateContext,
    GateResult,
    GateStatus,
    Severity,
)
from .engine.provider import (
    PROVIDER_REGISTRY,
    Provider,
    register_provider,
)
from .engine.registry import GATE_REGISTRY, get_gate, register_gate
from .engine.runner import EvidencePackage, GateRunner

__version__ = "0.3.1"

__all__ = [
    "Gate",
    "Provider",
    "GateRunner",
    "GateContext",
    "GateResult",
    "GateStatus",
    "Severity",
    "Finding",
    "register_gate",
    "register_provider",
    "get_gate",
    "GATE_REGISTRY",
    "PROVIDER_REGISTRY",
    "PluginError",
    "plugin_errors",
    "EvidencePackage",
    "load_gate_config",
]
