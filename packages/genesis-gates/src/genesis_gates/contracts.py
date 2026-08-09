"""Load + validate against the JSON Schemas in `contracts/` (the SSOT).

Reused by the CLI (`genesis validate`) and by the control-plane on ingest.
"""
from __future__ import annotations

import json
import os
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


def contracts_dir() -> Path:
    """Locate the JSON Schema `contracts/` directory.

    Resolution order:
      1. ``GENESIS_CONTRACTS_DIR`` env override (packaged/installed deployments).
      2. Package-local ``genesis_gates/contracts`` — this is where the wheel
         force-includes the schemas (see pyproject ``force-include``).
      3. Repo-root ``contracts/`` — the source/editable-install layout, where the
         schemas live next to ``src/`` at ``packages/genesis-gates/contracts``.
    """
    env = os.environ.get("GENESIS_CONTRACTS_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    # (2) installed wheel: src/genesis_gates/contracts.py -> genesis_gates/contracts
    packaged = here.parent / "contracts"
    if packaged.is_dir():
        return packaged
    # (3) source layout: <pkg>/src/genesis_gates/contracts.py -> parents[2] == <pkg>
    return here.parents[2] / "contracts"


@cache
def _load_schema(name: str) -> dict[str, Any]:
    path = contracts_dir() / name
    with path.open() as fh:
        return json.load(fh)


@cache
def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(_load_schema(name))


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    """Return a list of human-readable errors (empty == valid)."""
    return _errors(_validator("manifest.schema.json"), manifest)


def validate_evidence(evidence: dict[str, Any]) -> list[str]:
    return _errors(_validator("evidence.schema.json"), evidence)


def _errors(validator: Draft202012Validator, instance: dict[str, Any]) -> list[str]:
    errors = []
    for err in sorted(validator.iter_errors(instance), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "(root)"
        errors.append(f"{loc}: {err.message}")
    return errors
