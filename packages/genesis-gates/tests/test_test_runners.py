"""Multi-ecosystem unit-tests gate: the node/go/cargo runners + the aggregation.

The gate used to be pytest-only, so a JS/TS repo — the most common AI-generated
stack — got ``skipped`` on the check that matters most. These tests pin the three
things that keeps honest:

1. ecosystem detection (including the ``npm init`` stub that only *looks* like a
   test script, and nested ``frontend/`` layouts),
2. count parsing from real vitest / jest / ``node --test`` / go / cargo output,
3. the aggregation matrix — one GateResult across every ecosystem present.

No test here shells out to a real toolchain: ``run_cmd`` is stubbed with captured
output samples, so the suite is deterministic on a machine with (or without) any
of node, go and cargo installed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from genesis_gates.engine.base import (
    Finding,
    GateContext,
    GateResult,
    GateStatus,
    Severity,
)
from genesis_gates.engine.provider import PROVIDER_REGISTRY, Provider
from genesis_gates.engine.registry import get_gate
from genesis_gates.gates.unit_tests import UnitTestsGate
from genesis_gates.providers import cargo_test, go_test, node_test
from genesis_gates.providers._test_runner import find_project, format_counts
from genesis_gates.util import CmdResult

RUNNER_KEYS = ["pytest", "node-test", "go-test", "cargo-test"]


def ctx(repo: str | Path, config: dict | None = None) -> GateContext:
    return GateContext(repo_path=str(repo), manifest={}, config=config or {})


# --- captured runner output ------------------------------------------------ #

VITEST_PASS = """
 RUN  v1.6.0 /repo/frontend

 ✓ src/lib/format.test.ts (3 tests) 4ms
 ✓ src/App.test.tsx (9 tests) 41ms

 Test Files  2 passed (2)
      Tests  12 passed (12)
   Start at  10:21:33
   Duration  1.02s
"""

VITEST_FAIL = """
 FAIL  src/App.test.tsx > renders the header
AssertionError: expected 'Hi' to be 'Hello'

 Test Files  1 failed | 1 passed (2)
      Tests  1 failed | 11 passed (12)
"""

# Colour codes survive piping when the runner guesses wrong about the TTY.
VITEST_PASS_ANSI = (
    "\x1b[32m Test Files \x1b[39m 1 passed (1)\n"
    "\x1b[2m      Tests \x1b[22m 7 passed (7)\n"
)

JEST_FAIL = """
Summary of all failing tests
 FAIL  src/sum.test.js
  ● adds numbers

Test Suites: 1 failed, 1 passed, 2 total
Tests:       1 failed, 11 passed, 12 total
Snapshots:   0 total
Time:        2.104 s
"""

JEST_PASS = """
PASS src/sum.test.js
Test Suites: 2 passed, 2 total
Tests:       12 passed, 12 total
Snapshots:   0 total
"""

NODE_TAP = """
ℹ tests 5
ℹ suites 1
# tests 5
# suites 1
# pass 4
# fail 1
# cancelled 0
# skipped 0
# todo 0
"""

GO_MIXED = """ok  \texample.com/app/handlers\t0.012s
?   \texample.com/app/cmd\t[no test files]
--- FAIL: TestParse (0.00s)
    parse_test.go:21: got 3, want 4
FAIL\texample.com/app/parser\t0.004s
FAIL
"""

GO_ALL_PASS = """ok  \texample.com/app/handlers\t0.012s
ok  \texample.com/app/store\t0.104s
"""

GO_NO_TESTS = """?   \texample.com/app\t[no test files]
?   \texample.com/app/cmd\t[no test files]
"""

CARGO_MIXED = """
running 5 tests
test result: ok. 5 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s

running 2 tests
test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s

   Doc-tests demo
test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""

CARGO_PASS = """
running 3 tests
test result: ok. 3 passed; 0 failed; 2 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""


# --- helpers --------------------------------------------------------------- #

def make_node_repo(
    root: Path, *, script: str | None = "vitest", subdir: str | None = None,
    lockfile: str | None = None, node_modules: bool = True,
) -> Path:
    """A minimal JS project; returns the directory holding package.json."""
    pkg_dir = root / subdir if subdir else root
    pkg_dir.mkdir(parents=True, exist_ok=True)
    scripts = {"build": "vite build"}
    if script is not None:
        scripts["test"] = script
    (pkg_dir / "package.json").write_text(json.dumps({"name": "app", "scripts": scripts}))
    if lockfile:
        (pkg_dir / lockfile).write_text("{}\n")
    if node_modules:
        (pkg_dir / "node_modules").mkdir(exist_ok=True)
    return pkg_dir


def stub_run_cmd(monkeypatch, module, *results: CmdResult) -> list[dict]:
    """Replace ``run_cmd`` in ``module`` with a queue of canned results; returns
    the list of recorded calls (``{"cmd", "cwd", "env", "timeout"}``)."""
    calls: list[dict] = []
    queue = list(results)

    def fake(cmd, *, cwd=None, timeout=None, env=None, input_text=None):
        calls.append({"cmd": cmd, "cwd": cwd, "timeout": timeout, "env": env})
        return queue.pop(0) if queue else CmdResult(rc=0, stdout="", stderr="")

    monkeypatch.setattr(module, "run_cmd", fake)
    return calls


def toolchain(monkeypatch, module, present: bool) -> None:
    monkeypatch.setattr(module, "which", lambda *names: "/usr/bin/stub" if present else None)


# --- ecosystem detection --------------------------------------------------- #

@pytest.mark.parametrize("marker", ["package.json", "go.mod", "Cargo.toml"])
def test_find_project_at_repo_root(tmp_path: Path, marker: str) -> None:
    (tmp_path / marker).write_text("{}")
    assert find_project(tmp_path, marker) == tmp_path


@pytest.mark.parametrize(
    ("marker", "subdir"),
    [("package.json", "frontend"), ("go.mod", "service"), ("Cargo.toml", "engine")],
)
def test_find_project_in_immediate_subdirectory(
    tmp_path: Path, marker: str, subdir: str
) -> None:
    (tmp_path / subdir).mkdir()
    (tmp_path / subdir / marker).write_text("{}")
    assert find_project(tmp_path, marker) == tmp_path / subdir


def test_find_project_prefers_root_over_subdirectory(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text("{}")
    assert find_project(tmp_path, "package.json") == tmp_path


def test_find_project_ignores_dependency_trees(tmp_path: Path) -> None:
    # A package.json inside node_modules/ is a dependency, not the project.
    for noise in ("node_modules", ".cache", "vendor"):
        (tmp_path / noise).mkdir()
        (tmp_path / noise / "package.json").write_text("{}")
    assert find_project(tmp_path, "package.json") is None


def test_find_project_prefers_a_candidate_that_matches(tmp_path: Path) -> None:
    for name in ("backend", "frontend"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "package.json").write_text("{}")
    chosen = find_project(tmp_path, "package.json", lambda d: d.name == "frontend")
    assert chosen == tmp_path / "frontend"
    # nothing matches -> first candidate, so "no tests here" is still reported
    assert find_project(tmp_path, "package.json", lambda d: False) == tmp_path / "backend"


def test_node_prefers_the_project_that_declares_tests(monkeypatch, tmp_path: Path) -> None:
    make_node_repo(tmp_path, subdir="backend", script=None)
    make_node_repo(tmp_path, subdir="frontend", script="vitest")
    toolchain(monkeypatch, node_test, present=True)
    calls = stub_run_cmd(monkeypatch, node_test, CmdResult(rc=0, stdout=VITEST_PASS, stderr=""))
    res = node_test.NodeTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.passed
    assert calls[0]["cwd"] == str(tmp_path / "frontend")


def test_find_project_returns_none_for_missing_repo(tmp_path: Path) -> None:
    assert find_project(tmp_path / "nope", "package.json") is None


def test_find_project_absent_marker(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    assert find_project(tmp_path, "go.mod") is None


# --- package.json test-script detection ------------------------------------ #

def test_real_test_script_detected(tmp_path: Path) -> None:
    make_node_repo(tmp_path, script="vitest run")
    assert node_test._test_script(tmp_path) == "vitest run"


def test_npm_default_stub_is_not_a_test_script(tmp_path: Path) -> None:
    # `npm init` writes this; running it would report a bogus test failure.
    make_node_repo(tmp_path, script='echo "Error: no test specified" && exit 1')
    assert node_test._test_script(tmp_path) is None


@pytest.mark.parametrize(
    "stub",
    [
        'echo "Error: no test specified" && exit 1',
        "echo 'Error: no test specified' && exit 1",
        'echo "error: no test specified" && exit 1',
    ],
)
def test_stub_variants_all_ignored(stub: str) -> None:
    assert node_test._is_stub_script(stub)


def test_script_that_merely_mentions_exit_1_is_kept() -> None:
    assert not node_test._is_stub_script("jest || exit 1")


def test_missing_or_blank_test_script(tmp_path: Path) -> None:
    make_node_repo(tmp_path, script=None)
    assert node_test._test_script(tmp_path) is None
    make_node_repo(tmp_path, script="   ")
    assert node_test._test_script(tmp_path) is None


def test_unparseable_package_json_is_not_a_test_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{not json")
    assert node_test._test_script(tmp_path) is None


@pytest.mark.parametrize(
    ("lockfile", "expected"),
    [
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("package-lock.json", "npm"),
        (None, "npm"),
    ],
)
def test_package_manager_from_lockfile(
    tmp_path: Path, lockfile: str | None, expected: str
) -> None:
    make_node_repo(tmp_path, lockfile=lockfile)
    assert node_test._package_manager(tmp_path) == expected


def test_npm_ci_only_with_a_lockfile(tmp_path: Path) -> None:
    make_node_repo(tmp_path, lockfile="package-lock.json")
    assert node_test._install_cmd(tmp_path, "npm")[:2] == ["npm", "ci"]
    make_node_repo(tmp_path / "other")
    assert node_test._install_cmd(tmp_path / "other", "npm")[:2] == ["npm", "install"]
    assert node_test._install_cmd(tmp_path, "pnpm") == ["pnpm", "install"]


# --- count parsing --------------------------------------------------------- #

@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (VITEST_PASS, {"passed": 12}),
        (VITEST_FAIL, {"passed": 11, "failed": 1}),
        (JEST_PASS, {"passed": 12}),
        (JEST_FAIL, {"passed": 11, "failed": 1}),
        (NODE_TAP, {"passed": 4, "failed": 1}),
        ("no summary here", {}),
    ],
)
def test_node_count_parsing(output: str, expected: dict) -> None:
    assert node_test.parse_counts(output) == expected


def test_node_counts_survive_ansi_colour() -> None:
    from genesis_gates.providers._test_runner import clean

    assert node_test.parse_counts(clean(VITEST_PASS_ANSI)) == {"passed": 7}


def test_jest_test_suites_line_is_not_mistaken_for_tests() -> None:
    # "Test Suites: 1 failed, 1 passed" would double-count if it matched.
    assert node_test.parse_counts(JEST_FAIL)["passed"] == 11


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (GO_MIXED, {"passed": 1, "failed": 1, "no_tests": 1, "failing_tests": 1}),
        (GO_ALL_PASS, {"passed": 2}),
        (GO_NO_TESTS, {"no_tests": 2}),
        ("", {}),
    ],
)
def test_go_count_parsing(output: str, expected: dict) -> None:
    assert go_test.parse_counts(output) == expected


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (CARGO_MIXED, {"passed": 6, "failed": 1, "skipped": 0}),
        (CARGO_PASS, {"passed": 3, "failed": 0, "skipped": 2}),
        ("", {}),
    ],
)
def test_cargo_count_parsing(output: str, expected: dict) -> None:
    assert cargo_test.parse_counts(output) == expected


def test_format_counts_hides_zeroes() -> None:
    assert format_counts({"passed": 12, "failed": 0, "skipped": 0}) == "12 passed"
    assert format_counts({"passed": 11, "failed": 1}) == "1 failed, 11 passed"
    assert format_counts({}) == ""


# --- node provider behaviour ----------------------------------------------- #

def test_node_missing_toolchain_is_skipped(monkeypatch, tmp_path: Path) -> None:
    make_node_repo(tmp_path)
    toolchain(monkeypatch, node_test, present=False)
    res = node_test.NodeTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.skipped
    assert "node not installed" in res.summary


def test_node_no_package_json_is_skipped(monkeypatch, tmp_path: Path) -> None:
    toolchain(monkeypatch, node_test, present=True)
    res = node_test.NodeTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.skipped
    assert "no package.json" in res.summary


def test_node_stub_script_is_skipped_not_failed(monkeypatch, tmp_path: Path) -> None:
    make_node_repo(tmp_path, script='echo "Error: no test specified" && exit 1')
    toolchain(monkeypatch, node_test, present=True)
    calls = stub_run_cmd(monkeypatch, node_test)
    res = node_test.NodeTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.skipped
    assert "no test script" in res.summary
    assert calls == []  # the stub script is never executed
    # present-but-untested is recorded, not silently dropped
    assert res.findings[0].title == "no tests to run"
    assert res.findings[0].severity is Severity.low


def test_node_passes_and_reports_counts(monkeypatch, tmp_path: Path) -> None:
    make_node_repo(tmp_path, subdir="frontend", lockfile="package-lock.json")
    toolchain(monkeypatch, node_test, present=True)
    calls = stub_run_cmd(monkeypatch, node_test, CmdResult(rc=0, stdout=VITEST_PASS, stderr=""))
    res = node_test.NodeTestProvider({"timeout": 60}).run(ctx(tmp_path))
    assert res.status is GateStatus.passed
    assert res.summary == "12 passed"
    # ran in frontend/, with CI=1 so vitest does not enter watch mode
    assert calls[0]["cmd"] == ["npm", "test"]
    assert calls[0]["cwd"] == str(tmp_path / "frontend")
    assert calls[0]["env"]["CI"] == "1"
    assert calls[0]["timeout"] == 60


def test_node_failing_suite_fails_with_finding(monkeypatch, tmp_path: Path) -> None:
    make_node_repo(tmp_path)
    toolchain(monkeypatch, node_test, present=True)
    stub_run_cmd(monkeypatch, node_test, CmdResult(rc=1, stdout="", stderr=JEST_FAIL))
    res = node_test.NodeTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.failed
    assert res.summary == "1 failed, 11 passed"
    assert res.findings[0].severity is Severity.high
    assert res.findings[0].raw["counts"] == {"passed": 11, "failed": 1}
    assert res.findings[0].remediation


def test_node_crash_without_counts_is_error(monkeypatch, tmp_path: Path) -> None:
    make_node_repo(tmp_path)
    toolchain(monkeypatch, node_test, present=True)
    stub_run_cmd(
        monkeypatch, node_test,
        CmdResult(rc=1, stdout="", stderr="Cannot find module 'vitest'"),
    )
    res = node_test.NodeTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.error
    assert "without a parseable result" in res.summary


def test_node_empty_suite_is_skipped(monkeypatch, tmp_path: Path) -> None:
    make_node_repo(tmp_path)
    toolchain(monkeypatch, node_test, present=True)
    stub_run_cmd(
        monkeypatch, node_test,
        CmdResult(rc=1, stdout="No test files found, exiting with code 1", stderr=""),
    )
    res = node_test.NodeTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.skipped
    assert res.findings[0].title == "no tests to run"


def test_node_installs_when_node_modules_missing(monkeypatch, tmp_path: Path) -> None:
    make_node_repo(tmp_path, lockfile="package-lock.json", node_modules=False)
    toolchain(monkeypatch, node_test, present=True)
    calls = stub_run_cmd(
        monkeypatch, node_test,
        CmdResult(rc=0, stdout="added 300 packages", stderr=""),
        CmdResult(rc=0, stdout=VITEST_PASS, stderr=""),
    )
    res = node_test.NodeTestProvider({"install_timeout": 42}).run(ctx(tmp_path))
    assert res.status is GateStatus.passed
    assert calls[0]["cmd"][:2] == ["npm", "ci"]
    assert calls[0]["timeout"] == 42
    assert calls[1]["cmd"] == ["npm", "test"]


def test_node_skips_install_when_node_modules_present(monkeypatch, tmp_path: Path) -> None:
    make_node_repo(tmp_path, node_modules=True)
    toolchain(monkeypatch, node_test, present=True)
    calls = stub_run_cmd(monkeypatch, node_test, CmdResult(rc=0, stdout=VITEST_PASS, stderr=""))
    node_test.NodeTestProvider().run(ctx(tmp_path))
    assert [c["cmd"] for c in calls] == [["npm", "test"]]


def test_node_failed_install_is_skipped_never_failed(monkeypatch, tmp_path: Path) -> None:
    make_node_repo(tmp_path, node_modules=False)
    toolchain(monkeypatch, node_test, present=True)
    calls = stub_run_cmd(
        monkeypatch, node_test,
        CmdResult(rc=1, stdout="", stderr="ERR! network unreachable"),
    )
    res = node_test.NodeTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.skipped  # an offline box is not a red suite
    assert "network unreachable" in res.summary
    assert len(calls) == 1  # tests were never attempted


def test_node_timeout_is_error(monkeypatch, tmp_path: Path) -> None:
    make_node_repo(tmp_path)
    toolchain(monkeypatch, node_test, present=True)
    stub_run_cmd(monkeypatch, node_test, CmdResult(rc=124, stdout="", stderr="", timed_out=True))
    res = node_test.NodeTestProvider({"timeout": 5}).run(ctx(tmp_path))
    assert res.status is GateStatus.error
    assert res.error == "timeout"


def test_node_skips_when_lockfile_manager_missing(monkeypatch, tmp_path: Path) -> None:
    make_node_repo(tmp_path, lockfile="pnpm-lock.yaml")
    monkeypatch.setattr(node_test, "which", lambda *names: None if "pnpm" in names else "/bin/x")
    res = node_test.NodeTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.skipped
    assert "pnpm not installed" in res.summary


# --- go / cargo provider behaviour ----------------------------------------- #

def test_go_no_module_is_skipped(monkeypatch, tmp_path: Path) -> None:
    toolchain(monkeypatch, go_test, present=True)
    res = go_test.GoTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.skipped
    assert "no go.mod" in res.summary


def test_go_missing_toolchain_is_skipped(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/app\n")
    toolchain(monkeypatch, go_test, present=False)
    res = go_test.GoTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.skipped
    assert "go not installed" in res.summary


def test_go_passes(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/app\n")
    toolchain(monkeypatch, go_test, present=True)
    calls = stub_run_cmd(monkeypatch, go_test, CmdResult(rc=0, stdout=GO_ALL_PASS, stderr=""))
    res = go_test.GoTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.passed
    assert res.summary == "2 package(s) passed"
    assert calls[0]["cmd"] == ["go", "test", "./..."]


def test_go_failure_reports_failing_tests(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/app\n")
    toolchain(monkeypatch, go_test, present=True)
    stub_run_cmd(monkeypatch, go_test, CmdResult(rc=1, stdout=GO_MIXED, stderr=""))
    res = go_test.GoTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.failed
    assert "1 failing test(s)" in res.findings[0].title
    assert "1 package(s) passed" in res.summary


def test_go_module_without_tests_is_skipped(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/app\n")
    toolchain(monkeypatch, go_test, present=True)
    stub_run_cmd(monkeypatch, go_test, CmdResult(rc=0, stdout=GO_NO_TESTS, stderr=""))
    res = go_test.GoTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.skipped
    assert "no test files" in res.summary
    assert res.findings[0].title == "no tests to run"


def test_cargo_no_crate_is_skipped(monkeypatch, tmp_path: Path) -> None:
    toolchain(monkeypatch, cargo_test, present=True)
    res = cargo_test.CargoTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.skipped
    assert "no Cargo.toml" in res.summary


def test_cargo_missing_toolchain_is_skipped(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'demo'\n")
    toolchain(monkeypatch, cargo_test, present=False)
    res = cargo_test.CargoTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.skipped
    assert "cargo not installed" in res.summary


def test_cargo_passes(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'demo'\n")
    toolchain(monkeypatch, cargo_test, present=True)
    calls = stub_run_cmd(monkeypatch, cargo_test, CmdResult(rc=0, stdout=CARGO_PASS, stderr=""))
    res = cargo_test.CargoTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.passed
    assert res.summary == "3 passed, 2 skipped"
    assert calls[0]["cmd"] == ["cargo", "test"]


def test_cargo_failure_is_failed(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'demo'\n")
    toolchain(monkeypatch, cargo_test, present=True)
    stub_run_cmd(monkeypatch, cargo_test, CmdResult(rc=101, stdout=CARGO_MIXED, stderr=""))
    res = cargo_test.CargoTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.failed
    assert res.summary == "1 failed, 6 passed"


def test_cargo_crate_without_tests_is_skipped(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'demo'\n")
    toolchain(monkeypatch, cargo_test, present=True)
    stub_run_cmd(
        monkeypatch, cargo_test,
        CmdResult(rc=0, stdout="test result: ok. 0 passed; 0 failed; 0 ignored;", stderr=""),
    )
    res = cargo_test.CargoTestProvider().run(ctx(tmp_path))
    assert res.status is GateStatus.skipped
    assert res.findings[0].title == "no tests to run"


# --- gate aggregation ------------------------------------------------------ #

def _stub_provider(label: str, result: GateResult, available: bool = True) -> type[Provider]:
    class _Stub(Provider):
        key = f"stub-{label}"
        ecosystem = label

        def is_available(self, ctx: GateContext) -> bool:
            return available

        def run(self, ctx: GateContext) -> GateResult:
            return result

    return _Stub


def install_stubs(monkeypatch, **by_label: GateResult | None) -> None:
    """Point the gate's four provider keys at stubs. ``None`` = unavailable."""
    labels = dict(zip(RUNNER_KEYS, ["python", "node", "go", "rust"], strict=True))
    for key, label in labels.items():
        result = by_label.get(label)
        if result is None:
            result = GateResult(gate=key, status=GateStatus.skipped, summary="unused")
            stub = _stub_provider(label, result, available=False)
        else:
            stub = _stub_provider(label, result)
        monkeypatch.setitem(PROVIDER_REGISTRY, key, stub)


def r(status: GateStatus, summary: str, findings: list[Finding] | None = None) -> GateResult:
    return GateResult(gate="", status=status, summary=summary, findings=findings or [])


PASSED = GateStatus.passed
FAILED = GateStatus.failed
SKIPPED = GateStatus.skipped
ERROR = GateStatus.error


@pytest.mark.parametrize(
    ("python", "node", "expected"),
    [
        (PASSED, PASSED, PASSED),
        (PASSED, SKIPPED, PASSED),
        (SKIPPED, PASSED, PASSED),
        (PASSED, FAILED, FAILED),
        (FAILED, PASSED, FAILED),
        (FAILED, ERROR, FAILED),  # a real failure outranks a broken runner
        (ERROR, PASSED, ERROR),
        (ERROR, SKIPPED, ERROR),
        (SKIPPED, SKIPPED, SKIPPED),
    ],
)
def test_aggregation_matrix(
    monkeypatch, tmp_path: Path, python: GateStatus, node: GateStatus, expected: GateStatus
) -> None:
    install_stubs(
        monkeypatch,
        python=r(python, f"py {python.value}"),
        node=r(node, f"js {node.value}"),
    )
    res = UnitTestsGate().run(ctx(tmp_path))
    assert res.status is expected


def test_summary_names_each_ecosystem_that_ran(monkeypatch, tmp_path: Path) -> None:
    install_stubs(
        monkeypatch,
        python=r(PASSED, "51 passed"),
        node=r(PASSED, "12 passed"),
        go=r(SKIPPED, "no go.mod found"),
    )
    res = UnitTestsGate().run(ctx(tmp_path))
    assert res.summary == "python: 51 passed · node: 12 passed"  # skips stay out of the way


def test_untested_ecosystem_stays_visible_in_the_summary(
    monkeypatch, tmp_path: Path
) -> None:
    """A green Python backend must not hide a JS frontend with no tests — the
    reference app's exact blind spot."""
    untested = r(SKIPPED, "package.json in frontend/ declares no test script",
                 [Finding(gate="", title="no tests to run", severity=Severity.low)])
    install_stubs(monkeypatch, python=r(PASSED, "79 passed"), node=untested)
    res = UnitTestsGate().run(ctx(tmp_path))
    assert res.status is PASSED
    assert res.summary == (
        "python: 79 passed · node: package.json in frontend/ declares no test script"
    )
    assert [f.title for f in res.findings] == ["node: no tests to run"]


def test_all_skipped_summary_explains_every_reason(monkeypatch, tmp_path: Path) -> None:
    install_stubs(
        monkeypatch,
        python=r(SKIPPED, "no tests found"),
        node=r(SKIPPED, "no package.json found"),
    )
    res = UnitTestsGate().run(ctx(tmp_path))
    assert res.status is SKIPPED
    assert "python: no tests found" in res.summary
    assert "node: no package.json found" in res.summary


def test_findings_are_merged_and_labelled(monkeypatch, tmp_path: Path) -> None:
    py_finding = Finding(gate="", title="2 failing test(s)", severity=Severity.high)
    js_finding = Finding(gate="", title="1 failing test(s)", severity=Severity.high)
    install_stubs(
        monkeypatch,
        python=r(FAILED, "2 failed, 3 passed", [py_finding]),
        node=r(FAILED, "1 failed, 11 passed", [js_finding]),
    )
    res = UnitTestsGate().run(ctx(tmp_path))
    assert res.status is FAILED
    assert [f.title for f in res.findings] == [
        "python: 2 failing test(s)", "node: 1 failing test(s)",
    ]
    assert {f.gate for f in res.findings} == {"unit-tests"}
    assert [f.raw["ecosystem"] for f in res.findings] == ["python", "node"]


def test_unavailable_toolchain_counts_as_skipped(monkeypatch, tmp_path: Path) -> None:
    # node/go/cargo unavailable (is_available False) + python green -> passed.
    install_stubs(monkeypatch, python=r(PASSED, "3 passed"))
    res = UnitTestsGate().run(ctx(tmp_path))
    assert res.status is PASSED
    assert res.summary == "python: 3 passed"


def test_errors_are_collected_onto_the_gate_result(monkeypatch, tmp_path: Path) -> None:
    broken = GateResult(gate="", status=ERROR, summary="crashed", error="boom")
    install_stubs(monkeypatch, node=broken)
    res = UnitTestsGate().run(ctx(tmp_path))
    assert res.status is ERROR
    assert res.error == "node: boom"


def test_summary_is_bounded(monkeypatch, tmp_path: Path) -> None:
    install_stubs(monkeypatch, python=r(PASSED, "x" * 900))
    res = UnitTestsGate().run(ctx(tmp_path))
    assert len(res.summary) <= 400


# --- gate wiring / config -------------------------------------------------- #

def test_gate_declares_every_runner() -> None:
    assert get_gate("unit-tests") is UnitTestsGate
    assert UnitTestsGate.providers == RUNNER_KEYS


def test_runners_are_registered() -> None:
    assert PROVIDER_REGISTRY["node-test"] is node_test.NodeTestProvider
    assert PROVIDER_REGISTRY["go-test"] is go_test.GoTestProvider
    assert PROVIDER_REGISTRY["cargo-test"] is cargo_test.CargoTestProvider


def test_explicit_provider_config_bypasses_aggregation(tmp_path: Path) -> None:
    # BYO / defer-to-CI still pins the gate to exactly one provider.
    config = {"gates": {"unit-tests": {"provider": "external", "attested_by": "Acme CI"}}}
    res = UnitTestsGate().run(ctx(tmp_path, config))
    assert res.status is GateStatus.passed
    assert res.summary == "attested externally by Acme CI"


def test_explicit_unsupported_provider_still_raises(tmp_path: Path) -> None:
    config = {"gates": {"unit-tests": {"provider": "gitleaks"}}}
    with pytest.raises(ValueError):
        UnitTestsGate().run(ctx(tmp_path, config))


def test_real_gate_on_a_python_repo_is_not_skipped(tmp_path: Path) -> None:
    """End-to-end with the real providers: a python-only repo still passes, and
    the absent ecosystems do not drag the gate to skipped."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    res = UnitTestsGate().run(ctx(tmp_path))
    assert res.status is GateStatus.passed
    assert res.summary.startswith("python: ")
