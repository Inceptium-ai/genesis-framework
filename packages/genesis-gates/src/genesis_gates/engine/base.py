"""Core gate interface + result types.

A *gate* is a single check that takes a repo + manifest and returns a GateResult
(status + findings). Concrete gates live in `genesis_gates.gates` and subclass `Gate`.
This mirrors Genesis_core's Module SDK (`genesis_module.base.BasePipelineModule`),
simplified to a synchronous, dependency-light contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .provider import Provider


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


class GateStatus(str, Enum):
    passed = "passed"
    failed = "failed"
    skipped = "skipped"  # gate not applicable / tool not installed
    error = "error"  # gate crashed


@dataclass
class Finding:
    gate: str
    title: str
    severity: Severity
    detail: str = ""
    location: str | None = None
    remediation: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "title": self.title,
            "severity": self.severity.value,
            "detail": self.detail,
            "location": self.location,
            "remediation": self.remediation,
        }


@dataclass
class GateResult:
    gate: str
    status: GateStatus
    summary: str = ""
    findings: list[Finding] = field(default_factory=list)
    duration_s: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "status": self.status.value,
            "summary": self.summary,
            "duration_s": round(self.duration_s, 3),
            "error": self.error,
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class GateContext:
    """Everything a gate needs to run.

    ``config`` is the parsed ``.genesis/config.yaml`` (``{defaults, gates}``); use
    ``gate_settings(name)`` to get the merged block for one gate.
    """

    repo_path: str
    manifest: dict[str, Any]
    config: dict[str, Any] = field(default_factory=dict)

    def gate_settings(self, name: str) -> dict[str, Any]:
        """Merged config for a gate: ``defaults`` overlaid by ``gates.<name>``."""
        defaults = self.config.get("defaults") or {}
        gate_cfg = (self.config.get("gates") or {}).get(name) or {}
        return {**defaults, **gate_cfg}


class Gate:
    """Base class for all gates.

    A gate declares WHAT must be checked (its ``name``, matching the manifest's
    validation vocabulary) and the ordered ``providers`` (built-in provider keys)
    that can produce it. The default ``run`` resolves a provider — from
    ``.genesis/config.yaml`` or the first available built-in — and delegates to it,
    then applies the gate's ``fail_on_severity`` policy.

    A gate may still override ``run`` for bespoke logic; if it does, it is
    responsible for its own tool handling (this is how the original gates worked
    before the provider split).
    """

    name: str = "gate"
    description: str = ""
    providers: list[str] = []  # ordered built-in provider keys this gate supports

    def is_available(self) -> bool:  # noqa: D401 - simple predicate
        """Optimistic by default: real availability is decided per-provider in
        ``run`` (a gate with no available provider returns ``skipped``)."""
        return True

    def resolve_provider(
        self, ctx: GateContext, settings: dict[str, Any] | None = None
    ) -> Provider | None:
        """Pick the provider for this gate: an explicit `provider:` from config
        (a supported built-in or a generic command/report/external), else the
        first available built-in. Returns None if nothing is available.

        Raises ValueError if config names a provider this gate does not support
        (surfaced by the runner as an `error` result — config typos are visible).
        """
        from .provider import GENERIC_KEYS, PROVIDER_REGISTRY

        settings = settings if settings is not None else ctx.gate_settings(self.name)
        key = settings.get("provider")
        if key:
            if key in GENERIC_KEYS or key in self.providers:
                cls = PROVIDER_REGISTRY.get(key)
                if cls is None:
                    raise ValueError(
                        f"provider '{key}' for gate '{self.name}' is configured but not registered"
                    )
                return cls(settings)
            supported = list(self.providers) + sorted(GENERIC_KEYS)
            raise ValueError(
                f"gate '{self.name}' does not support provider '{key}' (supported: {supported})"
            )
        for k in self.providers:
            cls = PROVIDER_REGISTRY.get(k)
            if cls is None:
                continue
            provider = cls(settings)
            if provider.is_available(ctx):
                return provider
        return None

    def run(self, ctx: GateContext) -> GateResult:
        settings = ctx.gate_settings(self.name)
        provider = self.resolve_provider(ctx, settings)
        if provider is None:
            supported = ", ".join(self.providers) or "none"
            return GateResult(
                gate=self.name,
                status=GateStatus.skipped,
                summary=f"no available provider (built-ins: {supported})",
            )
        result = provider.run(ctx)
        # Stamp the gate name on the result + findings (providers are gate-agnostic).
        result.gate = self.name
        for finding in result.findings:
            finding.gate = self.name
        return self._apply_policy(result, settings)

    @staticmethod
    def _apply_policy(result: GateResult, settings: dict[str, Any]) -> GateResult:
        """Apply the gate-level `fail_on_severity` threshold, if set: the gate
        fails iff a finding is at least as severe as the threshold."""
        threshold = settings.get("fail_on_severity")
        if threshold and result.status in (GateStatus.passed, GateStatus.failed):
            order = list(Severity)
            try:
                thr_idx = order.index(Severity(threshold))
            except ValueError:
                return result
            worst_idx = min((order.index(f.severity) for f in result.findings), default=len(order))
            result.status = GateStatus.failed if worst_idx <= thr_idx else GateStatus.passed
        return result
