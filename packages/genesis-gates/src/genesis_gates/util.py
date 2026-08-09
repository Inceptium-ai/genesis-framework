"""Shared helpers for gates and providers: bounded subprocess execution and
severity mapping. stdlib only. Lives at the package root so both `genesis_gates.gates`
and `genesis_gates.providers` can import it without a circular dependency.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

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
) -> CmdResult:
    """Run `cmd`, never raising. Security scanners routinely exit non-zero when
    they find issues, so a non-zero rc is returned as data, not an exception.
    Only a missing binary or a timeout is flagged specially."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
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
