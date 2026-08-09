"""Concrete Genesis gates.

Importing this package (a) registers the providers, (b) auto-imports every gate
module in this directory, so each module's ``@register_gate`` runs, and (c)
loads third-party gates from the ``genesis_gates.gates`` entry-point group.
Drop a new ``<name>.py`` gate here and it is picked up automatically — no edits
here; ship one from your own package and declare the entry point — no fork.
"""
from __future__ import annotations

import importlib
import pkgutil

from .. import providers as _providers  # noqa: F401  (registers built-in + generic providers)
from .._plugins import load_gate_plugins

for _mod in pkgutil.iter_modules(__path__):
    if _mod.name.startswith("_"):
        continue  # skip _util and other private helpers
    # Safe: module names come from pkgutil over this package's own directory,
    # never from user input.
    # nosemgrep: python.lang.security.audit.non-literal-import.non-literal-import
    importlib.import_module(f"{__name__}.{_mod.name}")

# External gates load LAST, on purpose: the built-ins above already hold their
# names, and plugin registration is append-only, so no installed package can
# replace `secret-scan` & co. Failures are collected in
# ``genesis_gates.plugin_errors``, never raised (see genesis_gates/_plugins.py).
load_gate_plugins()
