"""go-test provider — run ``go test ./...`` for a Go module.

Go needs no dependency-install step of its own: the toolchain resolves modules
on demand, so the provider is just "is there a ``go.mod`` and a ``go`` binary".

``go test ./...`` prints one line per package — ``ok  pkg 0.01s``,
``FAIL  pkg 0.01s``, or ``?  pkg [no test files]`` — plus a ``--- FAIL: TestX``
line for each failing test. Counts are therefore reported per package, with the
failing-test count when the output offers one. A module where *every* package
says "no test files" is ``skipped``: there is nothing to run.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from ..engine.base import GateContext, GateResult
from ..engine.provider import Provider, register_provider
from ..util import run_cmd, which
from ._test_runner import combined, find_project, no_tests, outcome, skipped, timed_out

_OK_RE = re.compile(r"^ok\s+\S+", re.MULTILINE)
_FAIL_PKG_RE = re.compile(r"^FAIL\s+\S+", re.MULTILINE)
_NO_TESTS_RE = re.compile(r"^\?\s+\S+\s+\[no test files\]", re.MULTILINE)
_FAIL_TEST_RE = re.compile(r"^\s*--- FAIL:\s+(\S+)", re.MULTILINE)


def parse_counts(output: str) -> dict[str, int]:
    """Package-level counts from ``go test ./...`` output.

    ``passed``/``failed`` count *packages* (Go does not report per-test totals
    without ``-v``); ``failing_tests`` is the number of ``--- FAIL:`` lines, which
    Go does print even without ``-v``. ``no_tests`` counts packages with no test
    files, so the caller can tell "everything passed" from "nothing ran".
    """
    counts = {
        "passed": len(_OK_RE.findall(output)),
        "failed": len(_FAIL_PKG_RE.findall(output)),
        "no_tests": len(_NO_TESTS_RE.findall(output)),
        "failing_tests": len(_FAIL_TEST_RE.findall(output)),
    }
    return {k: v for k, v in counts.items() if v}


def _summarize(counts: dict[str, int]) -> str:
    parts = []
    if counts.get("failed"):
        parts.append(f"{counts['failed']} package(s) failed")
    if counts.get("failing_tests"):
        parts.append(f"{counts['failing_tests']} failing test(s)")
    if counts.get("passed"):
        parts.append(f"{counts['passed']} package(s) passed")
    if counts.get("no_tests"):
        parts.append(f"{counts['no_tests']} package(s) without tests")
    return ", ".join(parts)


@register_provider
class GoTestProvider(Provider):
    key = "go-test"
    ecosystem = "go"
    description = "Run `go test ./...` for a Go module and report pass/fail counts."

    def is_available(self, ctx: GateContext) -> bool:
        return which("go") is not None

    def run(self, ctx: GateContext) -> GateResult:
        start = time.perf_counter()
        repo = Path(ctx.repo_path)

        module_dir = find_project(repo, "go.mod")
        if module_dir is None:
            return skipped(self.key, "no go.mod found", start)
        if which("go") is None:
            return skipped(self.key, "go not installed", start)

        timeout = int(self.settings.get("timeout", 600))
        res = run_cmd(["go", "test", "./..."], cwd=str(module_dir), timeout=timeout)
        if res.timed_out:
            return timed_out(self.key, "go test", timeout, start)

        output = combined(res)
        counts = parse_counts(output)
        if not counts.get("passed") and not counts.get("failed") and counts.get("no_tests"):
            return no_tests(
                self.key,
                f"go module has no test files ({counts['no_tests']} package(s))",
                start,
                remediation="Add `_test.go` files so `go test ./...` has something to run.",
            )

        failing = counts.get("failing_tests") or counts.get("failed", 0)
        return outcome(
            self.key, tool="go test", rc=res.rc, counts=counts,
            summary=_summarize(counts) or f"go test exited {res.rc} (no package results)",
            output=output, start=start,
            failure_title=f"{failing} failing test(s)" if counts.get("failing_tests")
            else f"{counts.get('failed', 0)} failing package(s)",
        )
