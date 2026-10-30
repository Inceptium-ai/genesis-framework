"""Config loading/validation + provider resolution.

Decoupled from the real gates/providers (built by other components): this module
defines its own tiny ``Provider``/``Gate`` subclasses and registers them under
unique keys so it exercises the *machinery* without depending on the shipped
scanners existing yet.
"""
from __future__ import annotations

import pytest
from genesis_gates.config import load_gate_config, validate_gate_config
from genesis_gates.engine.base import Gate, GateContext, GateResult, GateStatus
from genesis_gates.engine.provider import PROVIDER_REGISTRY, Provider, register_provider
from genesis_gates.engine.registry import GATE_REGISTRY, register_gate


# --------------------------------------------------------------------------- #
# local dummy providers + gate (unique keys — no collision with real ones)
# --------------------------------------------------------------------------- #
@register_provider
class _DummyA(Provider):
    key = "test-dummy-a"

    def is_available(self, ctx: GateContext) -> bool:
        return True

    def run(self, ctx: GateContext) -> GateResult:  # pragma: no cover - unused
        return GateResult(gate="", status=GateStatus.passed)


@register_provider
class _DummyB(Provider):
    key = "test-dummy-b"

    def is_available(self, ctx: GateContext) -> bool:
        return True

    def run(self, ctx: GateContext) -> GateResult:  # pragma: no cover - unused
        return GateResult(gate="", status=GateStatus.passed)


@register_provider
class _DummyUnavailable(Provider):
    key = "test-dummy-unavailable"

    def is_available(self, ctx: GateContext) -> bool:
        return False

    def run(self, ctx: GateContext) -> GateResult:  # pragma: no cover - unused
        return GateResult(gate="", status=GateStatus.passed)


@register_gate
class _DummyGate(Gate):
    name = "test-dummy-gate"
    providers = ["test-dummy-a", "test-dummy-b"]

    def run(self, ctx: GateContext) -> GateResult:  # pragma: no cover - unused
        return GateResult(gate=self.name, status=GateStatus.passed)


@register_gate
class _DummyGateSkipFirst(Gate):
    # first built-in is unavailable → auto-select should fall through to the 2nd.
    name = "test-dummy-gate-skip"
    providers = ["test-dummy-unavailable", "test-dummy-b"]

    def run(self, ctx: GateContext) -> GateResult:  # pragma: no cover - unused
        return GateResult(gate=self.name, status=GateStatus.passed)


def _ctx(config: dict | None = None) -> GateContext:
    return GateContext(repo_path=".", manifest={}, config=config or {})


# --------------------------------------------------------------------------- #
# validate_gate_config
# --------------------------------------------------------------------------- #
def test_validate_accepts_valid_config():
    cfg = {
        "apiVersion": "genesis.dev/v0",
        "kind": "GateConfig",
        "defaults": {"fail_on_severity": "high"},
        "gates": {"test-dummy-gate": {"provider": "test-dummy-b"}},
    }
    assert validate_gate_config(cfg) == []


def test_validate_reports_bad_fail_on_severity():
    errors = validate_gate_config({"defaults": {"fail_on_severity": "nope"}})
    assert errors, "expected a validation error"
    assert any(e.startswith("defaults/fail_on_severity") for e in errors)


def test_validate_reports_unknown_root_key():
    errors = validate_gate_config({"totally-bogus": True})
    assert errors
    assert any(e.startswith("(root)") for e in errors)


# --------------------------------------------------------------------------- #
# load_gate_config
# --------------------------------------------------------------------------- #
def test_load_reads_config_file(tmp_path):
    cfg_dir = tmp_path / ".genesis"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        "apiVersion: genesis.dev/v0\n"
        "kind: GateConfig\n"
        "defaults:\n"
        "  fail_on_severity: medium\n"
    )
    loaded = load_gate_config(tmp_path)
    assert loaded["defaults"]["fail_on_severity"] == "medium"


def test_load_returns_empty_when_absent(tmp_path):
    assert load_gate_config(tmp_path) == {}


def test_load_reads_skip_dirs_per_gate(tmp_path):
    """`skip_dirs` is a documented knob, so the schema accepts it explicitly."""
    cfg_dir = tmp_path / ".genesis"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        "apiVersion: genesis.dev/v0\n"
        "kind: GateConfig\n"
        "gates:\n"
        "  dependency-scan:\n"
        "    skip_dirs: [vendor, third_party/generated]\n"
    )
    loaded = load_gate_config(tmp_path)
    assert loaded["gates"]["dependency-scan"]["skip_dirs"] == [
        "vendor", "third_party/generated"]


def test_validate_rejects_non_string_skip_dirs():
    errors = validate_gate_config({"gates": {"sast": {"skip_dirs": ["ok", 7]}}})
    assert any("skip_dirs" in e for e in errors)
    assert not validate_gate_config({"gates": {"sast": {"skip_dirs": ["ok"]}}})


def test_load_raises_on_invalid(tmp_path):
    cfg_dir = tmp_path / ".genesis"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text("defaults:\n  fail_on_severity: not-a-severity\n")
    with pytest.raises(ValueError):
        load_gate_config(tmp_path)


def test_load_honors_explicit_path(tmp_path):
    explicit = tmp_path / "custom.yaml"
    explicit.write_text("defaults:\n  timeout: 42\n")
    loaded = load_gate_config(tmp_path, explicit=explicit)
    assert loaded["defaults"]["timeout"] == 42


# --------------------------------------------------------------------------- #
# GateContext.gate_settings
# --------------------------------------------------------------------------- #
def test_gate_settings_merges_defaults_and_overrides():
    ctx = _ctx(
        {
            "defaults": {"fail_on_severity": "high", "timeout": 30},
            "gates": {"test-dummy-gate": {"fail_on_severity": "low", "provider": "x"}},
        }
    )
    settings = ctx.gate_settings("test-dummy-gate")
    # per-gate override wins over defaults …
    assert settings["fail_on_severity"] == "low"
    # … while un-overridden defaults still flow through …
    assert settings["timeout"] == 30
    # … and per-gate-only keys are present.
    assert settings["provider"] == "x"


def test_gate_settings_empty_config_is_empty():
    assert _ctx().gate_settings("test-dummy-gate") == {}


# --------------------------------------------------------------------------- #
# Gate.resolve_provider
# --------------------------------------------------------------------------- #
def test_resolve_uses_configured_provider():
    ctx = _ctx({"gates": {"test-dummy-gate": {"provider": "test-dummy-b"}}})
    provider = _DummyGate().resolve_provider(ctx)
    assert provider is not None
    assert provider.key == "test-dummy-b"


def test_resolve_autopicks_first_available_builtin():
    provider = _DummyGate().resolve_provider(_ctx())
    assert provider is not None
    assert provider.key == "test-dummy-a"


def test_resolve_skips_unavailable_builtin():
    provider = _DummyGateSkipFirst().resolve_provider(_ctx())
    assert provider is not None
    assert provider.key == "test-dummy-b"


def test_resolve_raises_on_unsupported_provider():
    ctx = _ctx({"gates": {"test-dummy-gate": {"provider": "test-dummy-unavailable"}}})
    with pytest.raises(ValueError):
        _DummyGate().resolve_provider(ctx)


def test_resolve_accepts_generic_provider_for_any_gate():
    # generic keys (command/report/external) are supported even if not built-in.
    ctx = _ctx({"gates": {"test-dummy-gate": {"provider": "external"}}})
    # external is a generic key; it resolves only if such a provider is registered.
    if "external" in PROVIDER_REGISTRY:
        provider = _DummyGate().resolve_provider(ctx)
        assert provider is not None
        assert provider.key == "external"
    else:
        # not registered yet → configured-but-missing surfaces as ValueError.
        with pytest.raises(ValueError):
            _DummyGate().resolve_provider(ctx)


# keep the dummy registrations from leaking assumptions: they live under unique
# keys, so no teardown is required, but assert they landed where expected.
def test_dummies_registered_under_unique_keys():
    assert GATE_REGISTRY["test-dummy-gate"] is _DummyGate
    assert PROVIDER_REGISTRY["test-dummy-a"] is _DummyA
