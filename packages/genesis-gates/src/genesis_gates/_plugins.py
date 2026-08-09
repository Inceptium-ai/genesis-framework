"""Third-party plugin discovery for gates and providers.

Genesis' own gates and providers register through import-time decorators over
this package's own modules (the ``pkgutil`` self-scan in ``gates/__init__.py``
and ``providers/__init__.py``). This module adds the other half: any *installed
distribution* can contribute a gate or a provider by declaring an entry point —
no fork of this repo, no edit to any file here.

    # your-plugin/pyproject.toml
    [project.entry-points."genesis_gates.gates"]
    license-check = "my_plugin.gates.license_check:LicenseCheckGate"

    [project.entry-points."genesis_gates.providers"]
    my-scanner = "my_plugin.providers.my_scanner:MyScannerProvider"

The contract is deliberately small:

* ``ep.load()`` returning a :class:`~genesis_gates.engine.base.Gate` /
  :class:`~genesis_gates.engine.provider.Provider` **subclass** registers that
  class under its ``name`` / ``key``.
* Anything else (a module, a callable) is assumed to have registered itself via
  import side effects — the same ``@register_gate`` decorator the built-ins use.
  If the entry point in fact registered *nothing*, that is reported as an error
  rather than passed over: a plugin that silently does nothing is a bug.
* **Fail-soft but visible.** A plugin that raises on load never breaks the CLI;
  the failure lands in :data:`plugin_errors` for the caller to surface (e.g.
  ``genesis gates`` printing "1 plugin failed to load: name — reason").
* **Built-ins win.** See :func:`_revert_replacements`.

Discovery runs once, at import of ``genesis_gates.gates`` /
``genesis_gates.providers``, and is idempotent: re-running it replaces this
group's entries in :data:`plugin_errors` instead of appending duplicates.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from types import ModuleType
from typing import Any

from .engine.base import Gate
from .engine.provider import PROVIDER_REGISTRY, Provider, register_provider
from .engine.registry import GATE_REGISTRY, register_gate

#: Entry-point groups a plugin distribution can declare.
GATES_GROUP = "genesis_gates.gates"
PROVIDERS_GROUP = "genesis_gates.providers"


@dataclass(frozen=True)
class PluginError:
    """One plugin that did not load cleanly. Collected, never raised.

    ``kind`` is ``"error"`` (the plugin failed or registered nothing) or
    ``"conflict"`` (it tried to take a name that was already registered).
    """

    group: str
    name: str
    reason: str
    kind: str = "error"

    def __str__(self) -> str:
        return f"{self.name} ({self.group}) — {self.reason}"


#: Plugin load failures and name conflicts, in discovery order. Mutated in
#: place — callers may hold a reference (``genesis_gates.plugin_errors``).
plugin_errors: list[PluginError] = []


def _discover(group: str) -> list[Any]:
    """Entry points in ``group``; broken installed metadata is an error, not a crash."""
    try:
        return list(metadata.entry_points(group=group))
    except Exception as exc:  # pragma: no cover - malformed distribution metadata
        plugin_errors.append(PluginError(group, "<discovery>", f"{type(exc).__name__}: {exc}"))
        return []


def _revert_replacements(
    registry: dict[str, Any], before: dict[str, Any], group: str, ep_name: str
) -> None:
    """Governance property: registration is append-only for plugins.

    A plugin may add new names; it may never take over one that already exists.
    Built-ins are registered before any entry point is loaded, so this is what
    makes "built-ins win" true in the strong sense: an installed third-party
    package must not be able to re-register ``secret-scan`` as a no-op and
    thereby weaken every audit on the machine. Any replacement performed by
    import side effects is reverted here and recorded as a conflict.
    """
    for name, original in before.items():
        if registry.get(name) is not original:
            registry[name] = original
            plugin_errors.append(
                PluginError(
                    group,
                    ep_name,
                    f"refused to replace already-registered '{name}' (built-ins win)",
                    kind="conflict",
                )
            )


def _contributed(obj: Any, registry: dict[str, Any], before: dict[str, Any]) -> bool:
    """Did loading ``obj`` actually register something in ``registry``?

    A new key is proof. Failing that, an existing entry defined in the loaded
    object's module also counts — that is the legitimate case of two entry
    points pointing at one already-imported module.
    """
    if set(registry) - set(before):
        return True
    module = obj.__name__ if isinstance(obj, ModuleType) else getattr(obj, "__module__", "")
    if not module:
        return False
    return any(
        cls.__module__ == module or cls.__module__.startswith(f"{module}.")
        for cls in registry.values()
    )


def _load_group(
    group: str,
    base: type,
    registry: dict[str, Any],
    register: Callable[[Any], Any],
    name_attr: str,
) -> None:
    # Idempotent across reloads: this group's errors are recomputed, not appended.
    plugin_errors[:] = [e for e in plugin_errors if e.group != group]

    for ep in _discover(group):
        before = dict(registry)
        try:
            obj = ep.load()
        except Exception as exc:
            # Fail-soft: one bad plugin must not take the CLI down with it. A
            # partial import may still have replaced entries — undo that too.
            _revert_replacements(registry, before, group, ep.name)
            plugin_errors.append(PluginError(group, ep.name, f"{type(exc).__name__}: {exc}"))
            continue

        _revert_replacements(registry, before, group, ep.name)

        if isinstance(obj, type) and issubclass(obj, base):
            name = getattr(obj, name_attr, None)
            existing = registry.get(name)
            if existing is not None and existing is not obj:
                plugin_errors.append(
                    PluginError(
                        group,
                        ep.name,
                        f"refused to replace already-registered '{name}' (built-ins win)",
                        kind="conflict",
                    )
                )
                continue
            try:
                register(obj)
            except ValueError as exc:  # missing/placeholder name or key
                plugin_errors.append(PluginError(group, ep.name, str(exc)))
        elif not _contributed(obj, registry, before):
            plugin_errors.append(
                PluginError(
                    group,
                    ep.name,
                    f"loaded '{getattr(ep, 'value', obj)}' but registered no "
                    f"{base.__name__.lower()} (return the class, or register it on import)",
                )
            )


def load_gate_plugins() -> None:
    """Register third-party gates from the ``genesis_gates.gates`` group."""
    _load_group(GATES_GROUP, Gate, GATE_REGISTRY, register_gate, "name")


def load_provider_plugins() -> None:
    """Register third-party providers from the ``genesis_gates.providers`` group."""
    _load_group(PROVIDERS_GROUP, Provider, PROVIDER_REGISTRY, register_provider, "key")
