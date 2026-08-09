"""cargo-test provider — run ``cargo test`` for a Rust crate.

Cargo fetches and builds dependencies itself, so there is no separate install
step; the provider only needs a ``Cargo.toml`` and a ``cargo`` binary.

``cargo test`` prints one summary line per test binary (unit tests, each
integration test, doc-tests)::

    test result: ok. 5 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out

Those lines are summed. A crate whose binaries all report zero tests is
``skipped`` — an empty suite is not a passing suite.
"""
from __future__ import annotations

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

_RESULT_LINE_RE = re.compile(r"^test result:.*$", re.MULTILINE)
_COUNT_PAIR_RE = re.compile(r"(\d+)\s+(passed|failed|ignored)")

_KIND_ALIASES = {"ignored": "skipped"}


def parse_counts(output: str) -> dict[str, int]:
    """Sum the ``test result:`` lines cargo prints per test binary."""
    counts: dict[str, int] = {}
    for line in _RESULT_LINE_RE.findall(output):
        for num, kind in _COUNT_PAIR_RE.findall(line):
            kind = _KIND_ALIASES.get(kind, kind)
            counts[kind] = counts.get(kind, 0) + int(num)
    return counts


@register_provider
class CargoTestProvider(Provider):
    key = "cargo-test"
    ecosystem = "rust"
    description = "Run `cargo test` for a Rust crate and report pass/fail counts."

    def is_available(self, ctx: GateContext) -> bool:
        return which("cargo") is not None

    def run(self, ctx: GateContext) -> GateResult:
        start = time.perf_counter()
        repo = Path(ctx.repo_path)

        crate_dir = find_project(repo, "Cargo.toml")
        if crate_dir is None:
            return skipped(self.key, "no Cargo.toml found", start)
        if which("cargo") is None:
            return skipped(self.key, "cargo not installed", start)

        timeout = int(self.settings.get("timeout", 900))
        res = run_cmd(["cargo", "test"], cwd=str(crate_dir), timeout=timeout)
        if res.timed_out:
            return timed_out(self.key, "cargo test", timeout, start)

        output = combined(res)
        counts = parse_counts(output)
        if res.rc == 0 and not any(counts.values()):
            return no_tests(
                self.key, "crate has no tests", start,
                remediation="Add `#[test]` functions or a `tests/` directory to the crate.",
            )

        return outcome(
            self.key, tool="cargo test", rc=res.rc, counts=counts,
            summary=format_counts(counts) or f"cargo test exited {res.rc} (no test results)",
            output=output, start=start,
        )
