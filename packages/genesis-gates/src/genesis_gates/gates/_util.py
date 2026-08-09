"""Backward-compat shim: helpers moved to ``genesis_gates.util``.

Kept so existing ``from ._util import ...`` imports keep working; new code should
import from ``genesis_gates.util`` directly.
"""
from __future__ import annotations

from ..util import DEFAULT_TIMEOUT, CmdResult, map_severity, run_cmd, which  # noqa: F401

__all__ = ["CmdResult", "DEFAULT_TIMEOUT", "map_severity", "run_cmd", "which"]
