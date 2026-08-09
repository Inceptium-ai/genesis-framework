"""Provider implementations (built-in scanners + generic BYO providers).

Importing this package auto-imports every module in it, so each provider
registers itself in ``genesis_gates.engine.provider.PROVIDER_REGISTRY`` on import.
Drop a new ``<name>.py`` here that calls ``@register_provider`` and it is picked
up automatically — no edits to this file.
"""
from __future__ import annotations

import importlib
import pkgutil

for _mod in pkgutil.iter_modules(__path__):
    # Safe: module names come from pkgutil over this package's own directory,
    # never from user input.
    # nosemgrep: python.lang.security.audit.non-literal-import.non-literal-import
    importlib.import_module(f"{__name__}.{_mod.name}")
