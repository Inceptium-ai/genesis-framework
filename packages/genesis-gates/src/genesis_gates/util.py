"""Shared helpers for gates and providers: bounded subprocess execution,
severity mapping and the shared `skip_dirs` scope setting. stdlib only. Lives at
the package root so both `genesis_gates.gates` and `genesis_gates.providers` can
import it without a circular dependency.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .engine.base import Severity

DEFAULT_TIMEOUT = 120


@dataclass
class CmdResult:
    """Outcome of a bounded subprocess run. `launched` is False when the binary
    was not found; `timed_out` is True when it exceeded the timeout. Neither of
    those is inferable from `rc` alone."""

    rc: int
    stdout: str
    stderr: str
    launched: bool = True
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        """Ran to completion (any exit code) without timing out or vanishing."""
        return self.launched and not self.timed_out


def run_cmd(
    cmd: list[str],
    *,
    cwd: str | None = None,
    input_text: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
) -> CmdResult:
    """Run `cmd`, never raising. Security scanners routinely exit non-zero when
    they find issues, so a non-zero rc is returned as data, not an exception.
    Only a missing binary or a timeout is flagged specially.

    `env` is *overlaid* on the current environment (not a replacement), so a
    caller can set e.g. `CI=1` without having to reconstruct PATH."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            env={**os.environ, **env} if env else None,
        )
    except FileNotFoundError:
        return CmdResult(rc=127, stdout="", stderr="binary not found", launched=False)
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        err = e.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return CmdResult(rc=124, stdout=out, stderr=err, timed_out=True)
    return CmdResult(rc=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def which(*names: str) -> str | None:
    """Return the first of `names` found on PATH, else None."""
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


# Common scanner severity vocabularies -> Genesis Severity.
_SEVERITY_MAP = {
    "critical": Severity.critical,
    "high": Severity.high,
    "error": Severity.high,
    "medium": Severity.medium,
    "moderate": Severity.medium,
    "warning": Severity.medium,
    "low": Severity.low,
    "info": Severity.info,
    "informational": Severity.info,
    "note": Severity.info,
    "unknown": Severity.medium,
    "negligible": Severity.low,
}


def map_severity(value: str | None, default: Severity = Severity.medium) -> Severity:
    """Map an arbitrary tool severity string to a Genesis Severity."""
    if not value:
        return default
    return _SEVERITY_MAP.get(str(value).strip().lower(), default)


def configured_skip_dirs(settings: Mapping[str, Any]) -> list[str]:
    """Normalize the optional ``skip_dirs`` gate setting into a list of dirs.

    ``skip_dirs`` is a list of **repo-relative directories a scanner must not
    look at**, set per gate in ``.genesis/config.yaml``::

        gates:
          dependency-scan:
            skip_dirs: ["vendor", "third_party/generated"]
          sast:
            skip_dirs: ["vendor"]

    It exists for trees the repo does not author and is not accountable for —
    vendored dependencies, generated output, packaged sample/fixture apps that
    are scanned in their own right when they are instantiated. It is *not* a
    way to hide findings in your own code: a skipped directory is invisible to
    the gate, so every entry should carry a comment a reviewer can audit, and
    the real attack surface (your source, infra and CI) must stay in scope.

    Each provider maps this to its scanner's own exclusion flag (trivy
    ``--skip-dirs``, semgrep ``--exclude``) rather than post-filtering
    findings, so the evidence package records what the scanner actually looked
    at. Providers whose scanner has its own repo-level allowlist file (e.g.
    gitleaks' ``.gitleaks.toml``) do not implement this.

    Accepts a single string or a list of strings; blank entries and duplicates
    are dropped (order preserved) and a leading ``./`` or trailing ``/`` is
    stripped, so ``"./catalog/"`` and ``"catalog"`` mean the same thing. An
    absolute path or one containing ``..`` is rejected rather than passed on:
    the scanners interpret these inconsistently, so it would read like scope
    that silently does not apply.
    """
    raw = settings.get("skip_dirs")
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ValueError(
            f"skip_dirs must be a string or a list of strings, got {type(raw).__name__}"
        )
    out: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            raise ValueError(
                f"skip_dirs entries must be strings, got {type(entry).__name__}"
            )
        cleaned = entry.strip().rstrip("/")
        while cleaned.startswith("./"):
            cleaned = cleaned[2:]
        if cleaned.startswith("/") or ".." in Path(cleaned).parts:
            raise ValueError(
                f"skip_dirs entries must be repo-relative directories, got {entry!r}"
            )
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out
