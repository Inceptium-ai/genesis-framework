# genesis-cli — the Genesis Agent Workbench CLI

Make any repository agent-ready, and keep AI-generated changes governed.

## Install

```bash
pip install genesis-cli       # or: uv tool install genesis-cli
genesis --version
```

Python 3.12+. `genesis-gates` (the engine) comes along as a dependency; there is
nothing else to install and no API key to configure.

> **Status:** PyPI publication happens at the first tagged release (`v0.1.0`),
> driven by `.github/workflows/release.yml` in the
> [genesis-framework](https://github.com/Inceptium-ai/genesis-framework)
> repository. Until that tag lands, install from source:

```bash
# from a clone of the monorepo / framework repo
uv sync && uv run genesis adopt .
# or, without cloning:
pip install "git+https://github.com/Inceptium-ai/genesis-framework#subdirectory=packages/genesis-cli"
```

## Commands

```bash
genesis --version             # installed version
genesis adopt [path]          # audit a repo: score, gaps, AGENTS.md draft
genesis adopt . --write       # also install AGENTS.md into the repo root
genesis adopt . --review      # add the AI second-reader (see below)
genesis adopt . --min-score 70   # CI mode: exit 1 below the threshold
genesis adopt . --json        # machine-readable report on stdout
genesis adopt . --baseline prev/report.json --fail-on-regression
                              # CI mode: diff against a previous report,
                              # exit 1 on regression (see below)
genesis gates [path]          # run just the delivery gates, per-gate table
genesis init [path]           # scaffold a new/bare repo agent-ready (see below)
```

## What `adopt` does

Deterministic, zero API keys, zero infrastructure:

1. **Delivery gates** — secret-scan, dependency-scan, unit-tests (in an isolated
   venv with the app's own requirements), SAST, SBOM. Gates skip cleanly when a
   scanner isn't installed.
2. **System completeness** — detects systems (auth, payments, file uploads)
   and checks their *mandatory flows* against the implementation surface
   (routes, files), not prose. This catches the classic AI-agent failure:
   an app that ships signup with no sign-in — or Stripe checkout with no
   webhook signature verification, uploads with no size limit.
3. **Env reconciliation** — the compose `${VAR}` contract and direct env reads,
   diffed for unset/undocumented variables.
4. **Structure** — tests, CI, lockfiles, `.env.example`, agent instructions.

Output: a 0–100 **readiness score** with grade, `report.html` (shareable),
`report.json` (machine-readable), `report.sarif` (SARIF 2.1.0 — feed it to
GitHub code scanning or any SARIF viewer), and an `AGENTS.md` draft that turns
the findings into instructions for AI coding agents.

## Baseline comparison (`--baseline`, `--fail-on-regression`)

Point `--baseline` at a previous run's `report.json` and `adopt` diffs the two
reports: score delta, grade change, new/resolved gate findings (identity:
gate + title + location) and new/resolved mandatory-flow gaps (identity:
profile + flow). The diff prints as a **CHANGES SINCE BASELINE** section and is
embedded in `report.json` as `baseline_comparison`.

```
CHANGES SINCE BASELINE
  score ▲ +9.2   grade F→D
  findings: 0 new, 0 resolved   ·   gaps: 0 new, 1 resolved
  − closed gap: Implement returning-user sign-in (login)
```

`--fail-on-regression` (requires `--baseline`) exits 1 when the run
**regressed**: the score dropped by more than 0.5 points, a new mandatory-flow
gap appeared, or a new critical/high finding appeared. It composes with
`--min-score` — either condition fails the run; when both flags pass, the exit
code is 0 regardless of grade.

### Score history (`history.jsonl`)

Every `adopt` run appends one line to `<out-dir>/history.jsonl` —
`{generated_at, score, grade, findings_count, gaps_count}` — append-only, never
rewritten. With two or more runs the terminal report ends with a trend line:

```
history: 44.8 → 49.0 → 75.5 (3 runs)
```

## `genesis gates` — just the delivery gates

`genesis gates [path]` runs the same deterministic gate set as `adopt`
(secret-scan, unit-tests, dependency-scan, sast, sbom) *without* the full
analysis — a fast check for "did my fix turn secret-scan green?". It prints a
per-gate table (name, status, summary) plus the top 5 findings — with location
and remediation — for each failed gate. Exit code: `0` when every selected
gate passed or skipped, `1` otherwise.

- `--gate NAME` (repeatable) limits the run to specific gates; names are
  validated against the gate registry, and a typo lists the valid names.
- `--json` prints the full evidence package instead of the table.

## `genesis init` — start agent-ready

For a new or bare repository, `genesis init [path]` scaffolds the files agents
need on day one: `AGENTS.md` (from a fresh audit of whatever exists — on an
empty directory its first task is "establish a test command"), `.gitignore`
(covers `.env*` and `.genesis-report/`), a `.env.example` stub, a
`.github/workflows/genesis-audit.yml` self-audit workflow, and a commented
`.genesis/config.yaml` gate config. It is idempotent and **never overwrites**
— existing files are reported as "exists, skipped".

## The AI second-reader (`--review`)

Optional pass that runs through the local `claude` CLI using your existing
Claude subscription login — still **no API key**. It is **additive-only**: it
may add findings, annotate (including flagging likely false positives),
prioritize, and write the executive summary. It can never remove or downgrade a
deterministic finding, and it never changes the score. If the CLI is missing or
the call fails, the audit completes without it.

## CI usage

```yaml
# .github/workflows/genesis-audit.yml
name: genesis-audit
on:
  pull_request:
  push: {branches: [main]}   # keeps the baseline cache fresh
permissions:
  contents: read
  security-events: write   # for the SARIF upload
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv sync
      # Restore the last report so PRs are gated on regressions, not just the
      # absolute score. The cache is (re)saved after every run on main.
      - uses: actions/cache@v4
        with:
          path: .genesis-baseline/
          key: genesis-baseline-${{ github.sha }}
          restore-keys: genesis-baseline-
      - run: |
          if [ -f .genesis-baseline/report.json ]; then
            uv run genesis adopt . --min-score 70 \
              --baseline .genesis-baseline/report.json --fail-on-regression
          else
            uv run genesis adopt . --min-score 70
          fi
      - run: mkdir -p .genesis-baseline &&
             cp .genesis-report/report.json .genesis-baseline/report.json
        if: github.ref == 'refs/heads/main'
      - uses: actions/upload-artifact@v4
        if: always()
        with: {name: genesis-report, path: .genesis-report/}
      # Findings land in the GitHub Security tab + inline PR annotations.
      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with: {sarif_file: .genesis-report/report.sarif}
```

Exit codes: `0` pass, `1` below threshold / regressed (or grade D/F when
neither `--min-score` nor `--fail-on-regression` is given), `2` usage error.
