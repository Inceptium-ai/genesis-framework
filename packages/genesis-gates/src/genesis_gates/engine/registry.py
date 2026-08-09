"""A tiny name->Gate registry so the CLI can select gates by id.

Concrete gates call `register_gate(MyGate)` at import time (see genesis_gates.gates).
"""
from __future__ import annotations

from .base import Gate

GATE_REGISTRY: dict[str, type[Gate]] = {}


def register_gate(gate_cls: type[Gate]) -> type[Gate]:
    """Class decorator / function to register a gate by its `name`."""
    name = getattr(gate_cls, "name", None)
    if not name or name == "gate":
        raise ValueError(f"{gate_cls.__name__} must define a unique `name`")
    GATE_REGISTRY[name] = gate_cls
    return gate_cls


def get_gate(name: str) -> type[Gate]:
    if name not in GATE_REGISTRY:
        raise KeyError(f"unknown gate '{name}'. Registered: {sorted(GATE_REGISTRY)}")
    return GATE_REGISTRY[name]
