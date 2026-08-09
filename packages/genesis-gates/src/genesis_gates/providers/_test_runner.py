"""Shared plumbing for the language test-runner providers.

Not a provider itself (the leading underscore says so) — ``node_test``,
``go_test`` and ``cargo_test`` import it for the parts that are identical across
ecosystems: finding the project inside a repo, cleaning tool output, and turning
an exit code plus parsed counts into a ``GateResult``.

Status semantics, identical for every runner (and matching ``pytest_provider``):

* exit 0 -> ``passed``
* non-zero with parseable counts -> ``failed`` (the finding carries the counts)
* non-zero with nothing parseable -> ``error`` (the runner itself broke)
* toolchain absent, no tests, or dependencies uninstallable -> ``skipped``

The last rule is the important one: a missing toolchain must never look like a
failing test suite.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path

from ..engine.base import Finding, GateResult, GateStatus, Severity
from ..util import CmdResult

# Directories that never contain the project we want to test — dependency and
# build trees, which are large and would make the depth-1 scan misleading.
_IGNORED_DIRS = frozenset({
    "node_modules", "vendor", "target", "dist", "build", "out",
    "venv", ".venv", "env", "__pycache__", "site-packages",
})

# Colour/cursor escapes; runners emit them even when piped, and they break counts.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

# How much runner output to keep on a finding for triage.
TAIL_CHARS = 1500


def find_projects(repo: Path, marker: str) -> list[Path]:
    """Directories holding ``marker`` (``package.json``, ``go.mod``, …): the repo
    root first, then each immediate subdirectory, in name order.

    Generated apps routinely split into ``frontend/`` + ``backend/``, so a
    root-only check would declare the most common AI-generated layout untestable.
    Depth 1 keeps the scan cheap and predictable — deeper monorepos should point
    a provider at the right directory via config.
    """
    if not repo.is_dir():
        return []
    found = [repo] if (repo / marker).is_file() else []
    for child in sorted(p for p in repo.iterdir() if p.is_dir()):
        if child.name.startswith(".") or child.name in _IGNORED_DIRS:
            continue
        if (child / marker).is_file():
            found.append(child)
    return found


def find_project(
    repo: Path, marker: str, prefer: Callable[[Path], bool] | None = None
) -> Path | None:
    """The project directory to test: the first candidate matching ``prefer``,
    else the first candidate at all, else None.

    ``prefer`` is how a repo with a tested ``frontend/`` and an untested
    ``backend/`` gets the tested one — falling back to the first candidate keeps
    the "found the ecosystem, but it has no tests" report accurate.
    """
    candidates = find_projects(repo, marker)
    if prefer is not None:
        for candidate in candidates:
            if prefer(candidate):
                return candidate
    return candidates[0] if candidates else None


def clean(text: str) -> str:
    """Strip ANSI escapes so counts parse regardless of the runner's TTY guess."""
    return _ANSI_RE.sub("", text)


def combined(res: CmdResult) -> str:
    """stdout + stderr, de-coloured. Jest writes its summary to stderr, vitest to
    stdout, so both streams have to be searched."""
    return clean(f"{res.stdout or ''}\n{res.stderr or ''}")


def format_counts(counts: dict[str, int]) -> str:
    """Human summary of a count dict: ``"1 failed, 11 passed"``.

    Only non-zero kinds appear, so a clean run reads ``"12 passed"`` rather than
    a wall of zeros. Empty counts produce ``""`` — callers substitute their own
    wording for "ran, but the output was not parseable".
    """
    order = ("failed", "error", "passed", "skipped")
    parts = [f"{counts[k]} {k}" for k in order if counts.get(k)]
    return ", ".join(parts)


def skipped(key: str, summary: str, start: float) -> GateResult:
    return GateResult(
        gate=key,
        status=GateStatus.skipped,
        summary=summary,
        duration_s=time.perf_counter() - start,
    )


def no_tests(key: str, summary: str, start: float, *, remediation: str) -> GateResult:
    """``skipped``, but with a finding: the ecosystem *is* in this repo, it just
    has no tests.

    That distinction is the whole point of the multi-ecosystem gate. "No Go code
    here" is silence worth keeping; "a React app with zero tests" is a fact the
    evidence package must carry, or the gate goes quiet on exactly the half of
    the repo nobody tested.
    """
    finding = Finding(
        gate=key,
        title="no tests to run",
        severity=Severity.low,
        detail=summary,
        remediation=remediation,
    )
    return GateResult(
        gate=key,
        status=GateStatus.skipped,
        summary=summary,
        findings=[finding],
        duration_s=time.perf_counter() - start,
    )


def timed_out(key: str, tool: str, timeout: int, start: float) -> GateResult:
    return GateResult(
        gate=key,
        status=GateStatus.error,
        summary=f"{tool} timed out after {timeout}s",
        error="timeout",
        duration_s=time.perf_counter() - start,
    )


def outcome(
    key: str,
    *,
    tool: str,
    rc: int,
    counts: dict[str, int],
    summary: str,
    output: str,
    start: float,
    failure_title: str | None = None,
) -> GateResult:
    """Turn a finished test run into a ``GateResult`` (see the module docstring
    for the status rules). ``summary`` is the caller's phrasing of ``counts``;
    ``counts`` decides failed-vs-error and the finding's title.
    """
    elapsed = time.perf_counter() - start
    if rc == 0:
        return GateResult(gate=key, status=GateStatus.passed, summary=summary,
                          duration_s=elapsed)

    failed = counts.get("failed", 0) + counts.get("error", 0)
    if failed == 0 and counts.get("passed", 0) == 0:
        # Non-zero exit and nothing recognisable ran: the runner broke (bad
        # config, compile error, missing dependency), which is not a test failure.
        return GateResult(
            gate=key,
            status=GateStatus.error,
            summary=f"{tool} exited {rc} without a parseable result",
            error=output.strip()[-TAIL_CHARS:] or None,
            duration_s=elapsed,
        )

    finding = Finding(
        gate=key,
        title=failure_title or f"{failed} failing test(s)",
        severity=Severity.high,
        detail=summary,
        raw={"counts": counts, "returncode": rc, "tail": output.strip()[-TAIL_CHARS:]},
        remediation="Fix failing unit tests before release.",
    )
    return GateResult(gate=key, status=GateStatus.failed, summary=summary,
                      findings=[finding], duration_s=elapsed)
