# AGENTS.md — Genesis Framework

Instructions for AI coding agents (Claude Code, Cursor, …) working in this
repository. Humans: see `README.md` and `CONTRIBUTING.md`.

## Commands

- **test**: `uv run pytest packages/genesis-gates/tests packages/genesis-cli/tests -q`
- **lint**: `uv run ruff check packages`
- **self-audit**: `uv run genesis adopt . --min-score 75`

Run the tests after every change. Do not commit with failing tests or lint.

## What this repository is

The open framework layer: gate engine, repo analysis (completeness profiles,
env reconciliation, readiness scoring, risk classification), JSON Schema
contracts, and the `genesis` CLI. The commercial Genesis platform builds on
these packages and lives elsewhere — **nothing here may import from it**
(`genesis_server`, `genesis_executor`). See `docs/open-core.md`.

## Ground rules

- **Determinism**: core analysis must produce identical results across runs.
  No network calls, no clocks, no randomness in `genesis_gates/analysis/`.
- **Fail-closed, honestly**: a gate that cannot run reports `skipped` with the
  reason — never a silent pass, never a false failure.
- **Suppressions carry reasons**: any `.trivyignore` entry needs a `statement`;
  any `# nosemgrep` needs an inline justification on the same line.
- **Schemas are contracts**: changes to `packages/genesis-gates/contracts/*.json`
  are additive-only within a major version.
- Match the surrounding style; add tests for anything behavioral.

## Extending

- **New gate/provider**: follow `genesis_gates/providers/` — register via the
  decorator, skip cleanly when the tool is absent, never raise on findings.
- **New system profile**: `genesis_gates/analysis/completeness.py` — markers +
  mandatory flows; include false-activation tests
  (`tests/test_completeness_profiles.py`).
