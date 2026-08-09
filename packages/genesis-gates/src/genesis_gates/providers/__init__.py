"""Provider implementations (built-in scanners + generic BYO providers).

Importing this package auto-imports every module in it, so each provider
registers itself in ``genesis_gates.engine.provider.PROVIDER_REGISTRY`` on import,
then loads third-party providers from the ``genesis_gates.providers`` entry-point
group. Drop a new ``<name>.py`` here that calls ``@register_provider`` and it is
picked up automatically — no edits to this file; ship one from your own package
and declare the entry point — no fork.
"""
from __future__ import annotations

import importlib
import pkgutil

from .._plugins import load_provider_plugins

for _mod in pkgutil.iter_modules(__path__):
    # Safe: module names come from pkgutil over this package's own directory,
    # never from user input.
    # nosemgrep: python.lang.security.audit.non-literal-import.non-literal-import
    importlib.import_module(f"{__name__}.{_mod.name}")

# External providers load LAST: built-in keys are already taken and plugin
# registration is append-only, so a plugin cannot swap out `gitleaks` for a
# provider that always passes. Failures land in ``genesis_gates.plugin_errors``.
load_provider_plugins()
