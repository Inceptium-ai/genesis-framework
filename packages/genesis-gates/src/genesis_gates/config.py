"""Load + validate the project gate config (`.genesis/config.yaml`).

This is the "how" file — provider selection, thresholds, commands, report paths —
kept separate from the sealed Delivery Manifest (the "what"). Absent file → `{}`
(gates auto-pick their default provider, i.e. today's behavior).
"""
from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from .contracts import contracts_dir

CONFIG_REL = Path(".genesis") / "config.yaml"


def config_path_for(repo_path: str | Path) -> Path:
    return Path(repo_path) / CONFIG_REL


def load_gate_config(repo_path: str | Path, explicit: str | Path | None = None) -> dict[str, Any]:
    """Return the parsed, validated gate config, or `{}` if none is present."""
    path = Path(explicit) if explicit else config_path_for(repo_path)
    if not path.is_file():
        return {}
    with path.open() as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: gate config must be a mapping, got {type(data).__name__}")
    errors = validate_gate_config(data)
    if errors:
        raise ValueError(f"invalid gate config {path}:\n  " + "\n  ".join(errors))
    return data


@functools.cache
def _validator() -> Draft202012Validator:
    with (contracts_dir() / "gateconfig.schema.json").open() as fh:
        return Draft202012Validator(json.load(fh))


def validate_gate_config(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for err in sorted(_validator().iter_errors(data), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "(root)"
        errors.append(f"{loc}: {err.message}")
    return errors
