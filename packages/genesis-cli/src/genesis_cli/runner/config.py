"""Runner configuration: ~/.genesis/runner.toml, mode 0600.

The file holds the server URL, the runner token (a credential — hence the
restrictive mode and the minimal writer that never round-trips through a
general serializer), and this machine's runner name.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

_FIELDS = ("server", "token", "name")


def config_dir() -> Path:
    return Path(os.environ.get("GENESIS_RUNNER_HOME", str(Path.home() / ".genesis")))


def config_path() -> Path:
    return config_dir() / "runner.toml"


def work_root() -> Path:
    """Where claimed task workspaces are materialized (and cleaned up)."""
    root = config_dir() / "runner-work"
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return {k: v for k, v in data.items() if k in _FIELDS and isinstance(v, str)}


def _toml_str(value: str) -> str:
    # Minimal TOML basic-string escaping: backslash and double quote.
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_config(server: str, token: str, name: str) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "# genesis runner credentials — created by `genesis runner connect`\n"
        f"server = {_toml_str(server)}\n"
        f"token = {_toml_str(token)}\n"
        f"name = {_toml_str(name)}\n"
    )
    # Create 0600 BEFORE writing the secret (never a world-readable window).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(body)
    os.chmod(path, 0o600)
    return path
