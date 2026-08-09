"""Third-party plugin discovery (entry points) for gates and providers.

The registries are process-global, so every test here runs under
``isolated_registries``: it snapshots ``GATE_REGISTRY`` / ``PROVIDER_REGISTRY`` /
``plugin_errors`` and restores them afterwards. Discovery itself is exercised
through a real ``importlib.reload`` of ``genesis_gates.gates`` /
``genesis_gates.providers`` with ``importlib.metadata.entry_points``
monkeypatched — i.e. the shipped wiring, not a hand-called loader.
"""
from __future__ import annotations

import importlib
import importlib.metadata
from collections.abc import Callable
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

import genesis_gates
import genesis_gates.gates
import genesis_gates.providers
import pytest
from genesis_gates._plugins import GATES_GROUP, PROVIDERS_GROUP, plugin_errors
from genesis_gates.engine.base import Gate, GateContext, GateResult, GateStatus
from genesis_gates.engine.provider import PROVIDER_REGISTRY, Provider, register_provider
from genesis_gates.engine.registry import GATE_REGISTRY, register_gate

# --- fake plugin payloads --------------------------------------------------- #


class LicenseCheckGate(Gate):
    """A well-behaved third-party gate, shipped by some other distribution."""

    name = "license-check"
    description = "Third-party gate used in tests."
    providers = []


class AcmeScannerProvider(Provider):
    key = "acme-scanner"
    description = "Third-party provider used in tests."

    def run(self, ctx: GateContext) -> GateResult:
        return GateResult(gate=self.key, status=GateStatus.passed, summary="ok")


class ShadowSecretScanGate(Gate):
    """A plugin trying to take over the built-in `secret-scan` name."""

    name = "secret-scan"
    description = "Always passes — exactly what governance must prevent."
    providers = []


class ShadowGitleaksProvider(Provider):
    key = "gitleaks"

    def run(self, ctx: GateContext) -> GateResult:  # pragma: no cover - never reached
        return GateResult(gate=self.key, status=GateStatus.passed, summary="ok")


@dataclass
class FakeEntryPoint:
    """Mimics ``importlib.metadata.EntryPoint`` (name/value/group/load())."""

    name: str
    group: str
    loader: Callable[[], Any]
    value: str = "fake_plugin.module:Object"
    loads: list[str] = field(default_factory=list)

    def load(self) -> Any:
        self.loads.append(self.name)
        return self.loader()


def _module(name: str = "acme_plugin.gates") -> ModuleType:
    """Stand-in for a plugin module that registered on import."""
    return ModuleType(name)


# --- fixtures --------------------------------------------------------------- #


@pytest.fixture
def isolated_registries():
    """Snapshot/restore all global registration state around a test."""
    gates_before = dict(GATE_REGISTRY)
    providers_before = dict(PROVIDER_REGISTRY)
    errors_before = list(plugin_errors)
    try:
        yield
    finally:
        GATE_REGISTRY.clear()
        GATE_REGISTRY.update(gates_before)
        PROVIDER_REGISTRY.clear()
        PROVIDER_REGISTRY.update(providers_before)
        plugin_errors[:] = errors_before


@pytest.fixture
def discover(monkeypatch, isolated_registries):
    """Run real discovery over the given fake entry points.

    ``discover(GATES_GROUP, ep, ...)`` patches ``entry_points`` and reloads the
    package whose ``__init__`` performs the load, so the assertion covers the
    shipped wiring (pkgutil self-scan first, plugins after).
    """

    def _run(group: str, *eps: FakeEntryPoint) -> None:
        def fake_entry_points(*, group: str | None = None, **_: Any) -> list[FakeEntryPoint]:
            return [ep for ep in eps if ep.group == group]

        monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
        package = genesis_gates.gates if group == GATES_GROUP else genesis_gates.providers
        importlib.reload(package)

    return _run


def _errors(group: str) -> list:
    return [e for e in plugin_errors if e.group == group]


# --- (a) a third-party gate class is registered ----------------------------- #


def test_third_party_gate_class_is_registered(discover):
    discover(GATES_GROUP, FakeEntryPoint("license-check", GATES_GROUP, lambda: LicenseCheckGate))

    assert GATE_REGISTRY["license-check"] is LicenseCheckGate
    assert not _errors(GATES_GROUP)
    # Built-ins are untouched by a plugin load.
    assert "secret-scan" in GATE_REGISTRY


def test_gate_registered_by_import_side_effect_is_accepted(discover):
    def loader():
        register_gate(LicenseCheckGate)  # what `@register_gate` does at import time
        return _module()

    discover(GATES_GROUP, FakeEntryPoint("license-check", GATES_GROUP, loader))

    assert GATE_REGISTRY["license-check"] is LicenseCheckGate
    assert not _errors(GATES_GROUP)


# --- (b) a third-party provider likewise ------------------------------------ #


def test_third_party_provider_class_is_registered(discover):
    discover(
        PROVIDERS_GROUP,
        FakeEntryPoint("acme-scanner", PROVIDERS_GROUP, lambda: AcmeScannerProvider),
    )

    assert PROVIDER_REGISTRY["acme-scanner"] is AcmeScannerProvider
    assert not _errors(PROVIDERS_GROUP)
    assert PROVIDER_REGISTRY["gitleaks"].key == "gitleaks"


def test_provider_registered_by_import_side_effect_is_accepted(discover):
    def loader():
        register_provider(AcmeScannerProvider)
        return _module("acme_plugin.providers")

    discover(PROVIDERS_GROUP, FakeEntryPoint("acme-scanner", PROVIDERS_GROUP, loader))

    assert PROVIDER_REGISTRY["acme-scanner"] is AcmeScannerProvider
    assert not _errors(PROVIDERS_GROUP)


# --- (c) a raising plugin is captured, never propagated --------------------- #


def test_raising_plugin_is_captured_and_does_not_propagate(discover):
    def boom():
        raise ImportError("no module named 'acme_internal'")

    # A broken plugin must not stop the ones after it, either.
    discover(
        GATES_GROUP,
        FakeEntryPoint("broken-gate", GATES_GROUP, boom),
        FakeEntryPoint("license-check", GATES_GROUP, lambda: LicenseCheckGate),
    )

    errors = _errors(GATES_GROUP)
    assert len(errors) == 1
    assert errors[0].name == "broken-gate"
    assert errors[0].kind == "error"
    assert "ImportError" in errors[0].reason and "acme_internal" in errors[0].reason
    assert "broken-gate" not in GATE_REGISTRY
    assert GATE_REGISTRY["license-check"] is LicenseCheckGate  # discovery continued


def test_plugin_that_registers_nothing_is_reported(discover):
    discover(GATES_GROUP, FakeEntryPoint("silent-gate", GATES_GROUP, lambda: _module()))

    errors = _errors(GATES_GROUP)
    assert len(errors) == 1
    assert errors[0].name == "silent-gate"
    assert "registered no gate" in errors[0].reason


def test_gate_without_a_name_is_reported(discover):
    class NamelessGate(Gate):
        pass  # `name` stays the placeholder "gate"

    discover(GATES_GROUP, FakeEntryPoint("nameless", GATES_GROUP, lambda: NamelessGate))

    errors = _errors(GATES_GROUP)
    assert len(errors) == 1 and "unique `name`" in errors[0].reason
    assert "gate" not in GATE_REGISTRY


def test_plugin_errors_are_recomputed_not_appended_on_reload(discover):
    def boom():
        raise RuntimeError("nope")

    ep = FakeEntryPoint("broken-gate", GATES_GROUP, boom)
    discover(GATES_GROUP, ep)
    discover(GATES_GROUP, ep)

    assert len(_errors(GATES_GROUP)) == 1


def test_plugin_errors_is_the_object_exported_from_the_package():
    # The CLI reads `genesis_gates.plugin_errors`; discovery mutates the list in
    # place, so the two must stay the same object.
    assert genesis_gates.plugin_errors is plugin_errors


# --- (d) built-ins win: a plugin may not replace a registered name ----------- #


def test_plugin_cannot_shadow_a_builtin_gate_class(discover):
    builtin = GATE_REGISTRY["secret-scan"]

    discover(
        GATES_GROUP, FakeEntryPoint("secret-scan", GATES_GROUP, lambda: ShadowSecretScanGate)
    )

    assert GATE_REGISTRY["secret-scan"] is builtin
    conflicts = [e for e in _errors(GATES_GROUP) if e.kind == "conflict"]
    assert len(conflicts) == 1
    assert "secret-scan" in conflicts[0].reason and "built-ins win" in conflicts[0].reason


def test_plugin_cannot_shadow_a_builtin_gate_via_import_side_effect(discover):
    builtin = GATE_REGISTRY["secret-scan"]

    def loader():
        register_gate(ShadowSecretScanGate)  # sneaks past the class-return path
        return _module()

    discover(GATES_GROUP, FakeEntryPoint("sneaky", GATES_GROUP, loader))

    assert GATE_REGISTRY["secret-scan"] is builtin
    conflicts = [e for e in _errors(GATES_GROUP) if e.kind == "conflict"]
    assert len(conflicts) == 1 and conflicts[0].name == "sneaky"


def test_plugin_cannot_shadow_a_builtin_provider(discover):
    builtin = PROVIDER_REGISTRY["gitleaks"]

    discover(
        PROVIDERS_GROUP,
        FakeEntryPoint("gitleaks", PROVIDERS_GROUP, lambda: ShadowGitleaksProvider),
    )

    assert PROVIDER_REGISTRY["gitleaks"] is builtin
    conflicts = [e for e in _errors(PROVIDERS_GROUP) if e.kind == "conflict"]
    assert len(conflicts) == 1


def test_shadowing_plugin_does_not_block_a_well_behaved_one(discover):
    builtin = GATE_REGISTRY["secret-scan"]

    discover(
        GATES_GROUP,
        FakeEntryPoint("secret-scan", GATES_GROUP, lambda: ShadowSecretScanGate),
        FakeEntryPoint("license-check", GATES_GROUP, lambda: LicenseCheckGate),
    )

    assert GATE_REGISTRY["secret-scan"] is builtin
    assert GATE_REGISTRY["license-check"] is LicenseCheckGate
