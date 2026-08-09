"""container-scan gate — scan the release container image for vulnerabilities.

A thin gate: it declares WHAT (a container-image vulnerability scan) and defers
the HOW to the ``trivy-image`` provider (or a generic BYO provider via config).
Self-skips cleanly when no image is configured or trivy is unavailable.
"""
from __future__ import annotations

from ..engine.base import Gate
from ..engine.registry import register_gate


@register_gate
class ContainerScanGate(Gate):
    name = "container-scan"
    description = "Scan the release container image for known vulnerabilities (trivy image)."
    providers = ["trivy-image"]
