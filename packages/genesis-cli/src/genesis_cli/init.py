"""`genesis init` — scaffold a new (or bare) repo so agents start agent-ready.

Idempotent: every file is created only if absent; existing files are reported
as skipped and never modified. AGENTS.md comes from a fresh audit of whatever
already exists, so on an empty directory it carries the "establish a test
command" first task, and on a partial repo it reflects real commands and gaps.
"""

from __future__ import annotations

from pathlib import Path

from .adopt import run_audit
from .agents_md import render_agents_md

GITIGNORE = """\
# Environment — never commit secrets (.env.example is the documented template).
.env
.env.*
!.env.example

# Genesis audit artifacts
.genesis-report/

# Dependencies & build output
node_modules/
dist/
build/
coverage/

# Python
__pycache__/
*.py[cod]
.venv/
venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# OS / editor noise
.DS_Store
"""

ENV_EXAMPLE = """\
# Environment contract — every variable the app reads, with safe example
# values. Copy to .env and fill in real values (never commit .env).
#
# EXAMPLE_API_KEY=changeme
"""

AUDIT_WORKFLOW = """\
# Agent-readiness audit: every PR runs `genesis adopt` and fails when the
# readiness score drops below the bar. Raise --min-score as remediation lands.
name: genesis-audit
on:
  pull_request:
  workflow_dispatch:

permissions:
  contents: read
  security-events: write  # for the SARIF upload

jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      # TODO: genesis-cli is not on PyPI yet. Until it is published, replace
      # this with an install from your checkout of the Genesis repo
      # (e.g. `pip install path/to/genesis/packages/genesis-cli`).
      - run: pip install genesis-cli
      - name: Agent-readiness audit
        run: genesis adopt . --min-score 40
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: genesis-readiness-report
          path: .genesis-report/
      # Surface findings in the GitHub Security tab / PR annotations.
      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with:
          sarif_file: .genesis-report/report.sarif
"""

GATE_CONFIG = """\
# Genesis gate config — the "how" of validation (provider selection,
# thresholds, BYO tooling). All optional: with everything commented out, each
# gate auto-picks its default provider and skips cleanly when the scanner is
# not installed. Schema: genesis-gates contracts/gateconfig.schema.json.
apiVersion: genesis.dev/v0
kind: GateConfig

# defaults:                    # applied to every gate, overridable per gate
#   fail_on_severity: high     # findings at/above this severity fail the gate
#   timeout: 300

# gates:
#   secret-scan:
#     provider: gitleaks
#   dependency-scan:
#     provider: trivy
#     fail_on_severity: critical
#   unit-tests:
#     provider: command
#     command: "npm test"
#     fail_on_nonzero: true
#   sast:
#     provider: report         # import a report your CI already produced
#     report: semgrep.sarif
#     parser: sarif
"""


def run_init(repo: Path) -> tuple[list[str], list[str]]:
    """Scaffold `repo`. Returns (created, skipped) as repo-relative paths."""
    repo.mkdir(parents=True, exist_ok=True)

    # Audit what exists BEFORE writing anything, so AGENTS.md reflects the
    # repo as found (empty dir -> "establish a test command" first task).
    agents_md = render_agents_md(run_audit(repo))

    created: list[str] = []
    skipped: list[str] = []
    for rel, content in (
        ("AGENTS.md", agents_md),
        (".gitignore", GITIGNORE),
        (".env.example", ENV_EXAMPLE),
        (".github/workflows/genesis-audit.yml", AUDIT_WORKFLOW),
        (".genesis/config.yaml", GATE_CONFIG),
    ):
        path = repo / rel
        if path.exists():
            skipped.append(rel)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            created.append(rel)
    return created, skipped


def render_summary(repo: Path, created: list[str], skipped: list[str]) -> str:
    lines = [f"genesis init — {repo}", ""]
    lines += [f"  created  {rel}" for rel in created]
    lines += [f"  exists, skipped  {rel}" for rel in skipped]
    lines += [
        "",
        "Next steps:",
        "  1. Fill in .env.example as you introduce configuration (never commit .env).",
        "  2. Establish a test command and record it in AGENTS.md — agents run it "
        "after every change.",
        "  3. Run `genesis adopt .` after your first commits to get a readiness "
        "score and gap list.",
    ]
    return "\n".join(lines)
