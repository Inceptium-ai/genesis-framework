"""CycloneDX (and SPDX-ish) SBOM parser.

An SBOM is not a pass/fail check — it is an inventory. So this parser does not
emit vulnerabilities; it emits a single ``info`` finding recording the component
count. The ``report`` provider treats an SBOM as always ``passed`` with this
informational finding attached.
"""
from __future__ import annotations

import json

from ..engine.base import Finding, Severity


def parse_cyclonedx(text: str) -> list[Finding]:
    """Count SBOM components and return one ``info`` finding.

    Supports CycloneDX ``components[]`` and SPDX ``packages[]``.
    """
    data = json.loads(text)
    components = data.get("components")
    if components is None:
        components = data.get("packages")  # SPDX-ish fallback
    count = len(components) if isinstance(components, list) else 0

    return [
        Finding(
            gate="",
            title=f"SBOM: {count} components",
            severity=Severity.info,
            detail=f"Software Bill of Materials lists {count} component(s).",
            raw={"component_count": count},
        )
    ]
