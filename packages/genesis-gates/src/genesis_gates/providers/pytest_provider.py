"""pytest provider — run the repo's pytest suite and report pass/fail counts.

Status: `passed` if the suite passes, `failed` if any test fails (findings carry
the counts), `skipped` if pytest is unavailable, the repo has no tests, or the
app's dependencies could not be installed, `error` if pytest crashed (e.g. a
collection error) without a clean summary.

Dependency isolation: a *generated* app declares its own requirements
(`requirements.txt`) and must be tested with THOSE installed — not against
whatever the Genesis host interpreter happens to have. When a requirements file
is found, this provider builds an isolated venv (outside the repo, so it never
pollutes test collection), installs the requirements + pytest there, and runs
pytest with that interpreter. If no requirements file is present it falls back to
the host interpreter's pytest.
"""
from __future__ import annotations

import hashlib
import re
import sys
import tempfile
import time
from pathlib import Path

from ..engine.base import Finding, GateContext, GateResult, GateStatus, Severity
from ..engine.provider import Provider, register_provider
from ..util import run_cmd, which

# e.g. "2 passed", "1 failed, 3 passed", "1 error", "5 passed, 2 skipped in 0.1s"
_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)")

# Where a generated app's Python deps live, relative to the repo root.
_REQ_CANDIDATES = ("requirements.txt", "backend/requirements.txt",
                   "api/requirements.txt", "server/requirements.txt")


def _has_tests(repo: Path) -> bool:
    if (repo / "tests").is_dir():
        return True
    return any(repo.glob("**/test_*.py")) or any(repo.glob("**/*_test.py"))


def _find_requirements(repo: Path) -> Path | None:
    for rel in _REQ_CANDIDATES:
        p = repo / rel
        if p.is_file():
            return p
    return None


def _venv_python(venv: Path) -> Path:
    # POSIX layout (macOS/Linux); Scripts/ on Windows.
    posix = venv / "bin" / "python"
    return posix if posix.exists() else venv / "Scripts" / "python.exe"


def _ensure_app_venv(repo: Path, requirements: Path) -> tuple[Path | None, str]:
    """Build (or reuse) an isolated venv with the app's requirements + pytest.

    Returns (venv_python, "") on success, or (None, reason) if the venv or the
    dependency install failed — the caller treats that as `skipped`, never a
    false test failure. The venv lives in the system temp dir keyed by the repo
    path + a hash of the requirements, so it is reused across resumes and stays
    out of pytest's collection tree.
    """
    req_text = requirements.read_text(errors="replace")
    key = hashlib.sha256(f"{repo}\n{req_text}".encode()).hexdigest()[:12]
    venv = Path(tempfile.gettempdir()) / f"genesis-gate-venv-{key}"
    py = _venv_python(venv)
    marker = venv / ".installed"

    if py.exists() and marker.exists():
        return py, ""  # cached — requirements unchanged

    if not venv.exists():
        made = run_cmd([sys.executable, "-m", "venv", str(venv)], timeout=120)
        if not made.ok or made.rc != 0:
            return None, f"venv creation failed: {(made.stderr or made.stdout)[:200]}"
    py = _venv_python(venv)
    if not py.exists():
        return None, "venv python not found after creation"

    install = run_cmd(
        [str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check",
         "-r", str(requirements), "pytest"],
        timeout=600,
    )
    if not install.ok or install.rc != 0:
        return None, f"dependency install failed: {(install.stderr or install.stdout)[-300:]}"
    marker.write_text("ok")
    return py, ""


def _pytest_cmd() -> list[str] | None:
    """Prefer the running interpreter's pytest (`python -m pytest`); fall back to
    a `pytest` binary on PATH. Returns None if neither is available."""
    probe = run_cmd([sys.executable, "-m", "pytest", "--version"], timeout=30)
    if probe.ok and probe.rc == 0:
        return [sys.executable, "-m", "pytest"]
    if which("pytest"):
        return ["pytest"]
    return None


def _parse_counts(output: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for num, kind in _COUNT_RE.findall(output):
        kind = "error" if kind == "errors" else kind
        counts[kind] = counts.get(kind, 0) + int(num)
    return counts


@register_provider
class PytestProvider(Provider):
    key = "pytest"
    description = "Run pytest in the target repo and report pass/fail counts."

    def is_available(self, ctx: GateContext) -> bool:
        # `python -m pytest` or a `pytest` binary satisfies availability.
        return _pytest_cmd() is not None

    def run(self, ctx: GateContext) -> GateResult:
        start = time.perf_counter()
        repo = Path(ctx.repo_path)

        if not _has_tests(repo):
            return GateResult(
                gate=self.key, status=GateStatus.skipped,
                summary="no tests found (no tests/ dir or test_*.py)",
                duration_s=time.perf_counter() - start,
            )

        # Isolated env when the app declares its own requirements; otherwise the
        # host interpreter's pytest.
        requirements = _find_requirements(repo)
        if requirements is not None:
            venv_py, reason = _ensure_app_venv(repo, requirements)
            if venv_py is None:
                return GateResult(
                    gate=self.key, status=GateStatus.skipped,
                    summary=f"could not prepare test env ({requirements.name}): {reason}",
                    duration_s=time.perf_counter() - start,
                )
            cmd = [str(venv_py), "-m", "pytest"]
        else:
            cmd = _pytest_cmd()
            if cmd is None:
                return GateResult(
                    gate=self.key, status=GateStatus.skipped,
                    summary="pytest not installed",
                    duration_s=time.perf_counter() - start,
                )

        timeout = int(self.settings.get("timeout", 300))
        res = run_cmd([*cmd, "-q"], cwd=str(repo), timeout=timeout)
        output = (res.stdout or "") + "\n" + (res.stderr or "")

        if res.timed_out:
            return GateResult(
                gate=self.key, status=GateStatus.error,
                summary=f"pytest timed out after {timeout}s", error="timeout",
                duration_s=time.perf_counter() - start,
            )

        counts = _parse_counts(output)
        passed = counts.get("passed", 0)
        failed = counts.get("failed", 0)
        errors = counts.get("error", 0)
        skipped = counts.get("skipped", 0)
        summary_counts = (
            f"{passed} passed, {failed} failed, {errors} errors, {skipped} skipped"
        )

        # pytest exit codes: 0 all passed; 1 tests failed; 2 interrupted;
        # 3 internal error; 4 usage error; 5 no tests collected.
        if res.rc == 5:
            return GateResult(
                gate=self.key, status=GateStatus.skipped,
                summary="pytest collected no tests",
                duration_s=time.perf_counter() - start,
            )

        if res.rc == 0:
            return GateResult(
                gate=self.key, status=GateStatus.passed,
                summary=summary_counts,
                duration_s=time.perf_counter() - start,
            )

        # Non-zero: distinguish genuine test failures from a tool crash.
        if failed == 0 and errors == 0 and passed == 0:
            return GateResult(
                gate=self.key, status=GateStatus.error,
                summary=f"pytest exited {res.rc} without a parseable result",
                error=(res.stderr or output).strip()[:500],
                duration_s=time.perf_counter() - start,
            )

        findings = [
            Finding(
                gate=self.key,
                title=f"{failed + errors} failing test(s)",
                severity=Severity.high,
                detail=summary_counts,
                raw={"counts": counts, "returncode": res.rc,
                     "tail": output.strip()[-1500:]},
                remediation="Fix failing unit tests before release.",
            )
        ]
        return GateResult(
            gate=self.key, status=GateStatus.failed,
            summary=summary_counts,
            findings=findings,
            duration_s=time.perf_counter() - start,
        )
