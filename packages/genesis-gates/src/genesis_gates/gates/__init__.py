"""Concrete Genesis gates.

Importing this package (a) registers the providers and (b) auto-imports every
gate module in this directory, so each module's ``@register_gate`` runs. Drop a
new ``<name>.py`` gate here and it is picked up automatically — no edits here.
"""
from __future__ import annotations

import importlib
import pkgutil

from .. import providers as _providers  # noqa: F401  (registers built-in + generic providers)

for _mod in pkgutil.iter_modules(__path__):
    if _mod.name.startswith("_"):
        continue  # skip _util and other private helpers
    # Safe: module names come from pkgutil over this package's own directory,
    # never from user input.
    # nosemgrep: python.lang.security.audit.non-literal-import.non-literal-import
    importlib.import_module(f"{__name__}.{_mod.name}")
