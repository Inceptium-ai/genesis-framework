"""Report/output parsers: normalize a tool report or command output into
``list[Finding]`` (with the gate name left blank — the gate stamps it).

``PARSERS`` maps a ``parser`` config key to its function. The generic ``report``
and ``command`` providers dispatch through this dict. Tool-native parser keys
(``trivy-json`` etc.) are declared in the config schema but not yet implemented;
they are listed in ``UNSUPPORTED_PARSERS`` so providers can emit a clear error
rather than a confusing KeyError.
"""
from __future__ import annotations

from collections.abc import Callable

from ..engine.base import Finding
from .cyclonedx import parse_cyclonedx
from .junit import parse_junit
from .sarif import parse_sarif

PARSERS: dict[str, Callable[[str], list[Finding]]] = {
    "sarif": parse_sarif,
    "junit": parse_junit,
    "cyclonedx": parse_cyclonedx,
}

# Tool-native formats declared in the schema but not yet implemented.
UNSUPPORTED_PARSERS = frozenset(
    {"trivy-json", "gitleaks-json", "syft-json", "pip-audit-json"}
)

__all__ = ["PARSERS", "UNSUPPORTED_PARSERS", "parse_sarif", "parse_junit", "parse_cyclonedx"]
