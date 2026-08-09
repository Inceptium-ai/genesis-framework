"""node-test provider — run a JS/TS project's own test script.

JavaScript/TypeScript is the most common stack an AI assistant generates, so a
unit-tests gate that only knows pytest silently skips the check that matters
most. This provider runs whatever the project already declares: ``scripts.test``
in ``package.json``, whether that is vitest, jest, mocha or ``node --test``.

What it does, in order:

1. Locate the project — repo root, else an immediate subdirectory with a
   ``package.json`` (generated apps put the JS half in ``frontend/``).
2. Require a *real* ``scripts.test``. ``npm init`` writes a stub that just
   exits 1 (``echo "Error: no test specified" && exit 1``); running it would
   report a bogus test failure, so it counts as "no tests" -> ``skipped``.
3. Install dependencies if ``node_modules/`` is missing, using the package
   manager the lockfile implies. An install that fails is ``skipped`` with the
   reason — a machine that is offline or a broken lockfile is not a test failure.
4. Run the test script with ``CI=1``, which is what makes vitest and jest run
   once instead of dropping into interactive watch mode.

Pass/fail comes from the **exit code** — every JS runner is reliable there.
Counts are parsed best-effort from vitest/jest/TAP summaries and only decorate
the report (and separate a real failure from a crash).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ..engine.base import GateContext, GateResult
from ..engine.provider import Provider, register_provider
from ..util import run_cmd, which
from ._test_runner import (
    combined,
    find_project,
    format_counts,
    no_tests,
    outcome,
    skipped,
    timed_out,
)

# `npm init` writes this placeholder; it is the absence of tests, not a failure.
_STUB_MARKERS = ("no test specified", "exit 1")

# Lockfile -> package manager. Order matters only for the (odd) repo with two.
_LOCKFILES = (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("package-lock.json", "npm"))

# jest: "Tests:       1 failed, 11 passed, 12 total"
# vitest: "Tests  1 failed | 11 passed (12)"  /  "Tests  12 passed (12)"
_TESTS_LINE_RE = re.compile(r"^[^\S\n]*Tests:?[^\S\n]+(\S.*)$", re.MULTILINE)
_COUNT_PAIR_RE = re.compile(r"(\d+)\s+(passed|failed|skipped|todo|pending)")
# `node --test` TAP epilogue: "# pass 11" / "# fail 1" / "# skipped 0"
_TAP_RE = re.compile(r"^#\s*(pass|fail|skipped)\s+(\d+)\s*$", re.MULTILINE)

_KIND_ALIASES = {"todo": "skipped", "pending": "skipped", "fail": "failed", "pass": "passed"}

# Non-interactive + uncoloured: CI is what flips vitest/jest out of watch mode.
_TEST_ENV = {"CI": "1", "NO_COLOR": "1", "FORCE_COLOR": "0"}


def _is_stub_script(script: str) -> bool:
    """True for npm's placeholder test script (in any of its quoting variants)."""
    lowered = script.lower()
    return all(marker in lowered for marker in _STUB_MARKERS)


def _test_script(pkg_dir: Path) -> str | None:
    """The project's real ``scripts.test``, or None (missing, stub, unreadable)."""
    try:
        pkg = json.loads((pkg_dir / "package.json").read_text(errors="replace"))
    except (OSError, ValueError):
        return None
    if not isinstance(pkg, dict):
        return None
    script = (pkg.get("scripts") or {}).get("test")
    if not isinstance(script, str) or not script.strip():
        return None
    return None if _is_stub_script(script) else script


def _package_manager(pkg_dir: Path) -> str:
    for lockfile, manager in _LOCKFILES:
        if (pkg_dir / lockfile).is_file():
            return manager
    return "npm"


def _install_cmd(pkg_dir: Path, manager: str) -> list[str]:
    if manager == "npm":
        # `npm ci` is the reproducible install, but it *requires* a lockfile.
        verb = "ci" if (pkg_dir / "package-lock.json").is_file() else "install"
        return ["npm", verb, "--no-audit", "--no-fund"]
    # pnpm/yarn already prefer the lockfile; plain `install` works on v1 and v3+.
    return [manager, "install"]


def parse_counts(output: str) -> dict[str, int]:
    """Best-effort test counts from vitest / jest / ``node --test`` output.

    TAP counters win when present (they are exact); otherwise the *last* summary
    line wins, since watch-free runs still print one block per invocation.
    """
    counts: dict[str, int] = {}
    for kind, num in _TAP_RE.findall(output):
        kind = _KIND_ALIASES.get(kind, kind)
        counts[kind] = counts.get(kind, 0) + int(num)
    if counts:
        return {k: v for k, v in counts.items() if v}

    lines = _TESTS_LINE_RE.findall(output)
    if not lines:
        return {}
    for num, kind in _COUNT_PAIR_RE.findall(lines[-1]):
        kind = _KIND_ALIASES.get(kind, kind)
        counts[kind] = counts.get(kind, 0) + int(num)
    return counts


@register_provider
class NodeTestProvider(Provider):
    key = "node-test"
    ecosystem = "node"
    description = "Run a JS/TS project's `npm test` script and report pass/fail counts."

    def is_available(self, ctx: GateContext) -> bool:
        return which("node") is not None

    def run(self, ctx: GateContext) -> GateResult:
        start = time.perf_counter()
        repo = Path(ctx.repo_path)

        if which("node") is None:
            return skipped(self.key, "node not installed", start)

        # Prefer a project that actually declares tests: `frontend/` with a test
        # script beats `backend/` with only a migrations script.
        pkg_dir = find_project(repo, "package.json", lambda d: _test_script(d) is not None)
        if pkg_dir is None:
            return skipped(self.key, "no package.json found", start)

        where = "" if pkg_dir == repo else f" in {pkg_dir.name}/"
        if _test_script(pkg_dir) is None:
            return no_tests(
                self.key,
                f"package.json{where} declares no test script "
                "(an `npm init` placeholder counts as none)",
                start,
                remediation="Add a `test` script to package.json (e.g. vitest, jest) "
                            "so the unit-tests gate covers the JS/TS code.",
            )

        manager = _package_manager(pkg_dir)
        if which(manager) is None:
            return skipped(self.key, f"{manager} not installed", start)

        if not (pkg_dir / "node_modules").is_dir():
            failure = self._install(pkg_dir, manager)
            if failure is not None:
                return skipped(self.key, failure, start)

        timeout = int(self.settings.get("timeout", 600))
        res = run_cmd([manager, "test"], cwd=str(pkg_dir), timeout=timeout, env=_TEST_ENV)
        if res.timed_out:
            return timed_out(self.key, f"{manager} test", timeout, start)

        output = combined(res)
        if res.rc != 0 and "No test files found" in output:
            # vitest exits non-zero on an empty suite; that is "no tests", not a failure.
            return no_tests(
                self.key, f"test script ran but matched no test files{where}", start,
                remediation="Add test files the project's runner can pick up "
                            "(e.g. `src/**/*.test.ts`).",
            )

        counts = parse_counts(output)
        summary = format_counts(counts) or (
            f"{manager} test exited {res.rc} (counts not reported)"
        )
        return outcome(
            self.key, tool=f"{manager} test", rc=res.rc, counts=counts,
            summary=summary, output=output, start=start,
        )

    def _install(self, pkg_dir: Path, manager: str) -> str | None:
        """Install dependencies. Returns None on success, else a skip reason."""
        timeout = int(self.settings.get("install_timeout", 900))
        cmd = _install_cmd(pkg_dir, manager)
        res = run_cmd(cmd, cwd=str(pkg_dir), timeout=timeout, env=_TEST_ENV)
        if res.timed_out:
            return f"`{' '.join(cmd)}` timed out after {timeout}s"
        if not res.ok or res.rc != 0:
            detail = combined(res).strip()[-300:] or "no output"
            return f"`{' '.join(cmd)}` failed: {detail}"
        return None
