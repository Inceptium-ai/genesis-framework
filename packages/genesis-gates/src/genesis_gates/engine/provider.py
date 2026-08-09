"""Provider interface — the *how* behind a gate.

A gate declares WHAT must be checked (e.g. `secret-scan`); a provider is a
concrete way to produce that check's findings — a bundled default scanner
(gitleaks, trivy, …) or a generic BYO provider (`command`, `report`, `external`).

Providers register in ``PROVIDER_REGISTRY`` by their ``key``; a gate resolves one
from ``.genesis/config.yaml`` or auto-picks the first available built-in
(see ``genesis_gates.engine.base.Gate.resolve_provider``).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from .base import GateResult

if TYPE_CHECKING:
    from .base import GateContext

# key -> Provider subclass (built-in scanners + generic providers all register here)
PROVIDER_REGISTRY: dict[str, type[Provider]] = {}

# generic providers usable by ANY gate (the BYO / defer-to-CI surface)
GENERIC_KEYS = frozenset({"command", "report", "external"})


def register_provider(cls: type[Provider]) -> type[Provider]:
    """Class decorator: register a provider by its unique ``key``."""
    key = getattr(cls, "key", None)
    if not key or key == "provider":
        raise ValueError(f"{cls.__name__} must define a unique `key`")
    PROVIDER_REGISTRY[key] = cls
    return cls


class Provider(ABC):
    """Base class for gate providers.

    Subclasses set a unique ``key`` and implement ``run``. Override
    ``is_available`` to self-skip when a binary/credential is missing (used for
    auto-selection). ``settings`` is the merged per-gate config block from
    ``.genesis/config.yaml`` (``defaults`` + ``gates.<name>``).
    """

    key: str = "provider"
    description: str = ""

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self.settings = settings or {}

    def is_available(self, ctx: GateContext) -> bool:  # noqa: D401 - predicate
        return True

    @abstractmethod
    def run(self, ctx: GateContext) -> GateResult:  # pragma: no cover - interface
        ...
