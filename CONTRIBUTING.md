# Contributing to Genesis Framework

Thanks for helping make AI-generated software provable.

## Ground rules

- **DCO, not CLA**: sign your commits (`git commit -s`) to certify the
  [Developer Certificate of Origin](https://developercertificate.org/).
- **This repo is a published mirror** of the Genesis monorepo. PRs are reviewed
  here, then imported upstream and land in the next sync (your authorship is
  preserved in the import commit). Small doc fixes may be batched.
- **The boundary rule**: nothing in this repo may import from the commercial
  Genesis platform. If a change needs a new primitive, it belongs here.

## What contributions land well

- New **gates** and **providers** (scanner integrations follow the pattern in
  `genesis_gates/providers/` — registered via decorator, skip cleanly when the
  tool is absent, never raise on findings).
- New **system profiles** (`genesis_gates/analysis/completeness.py`): a profile
  = markers + mandatory flows + surface patterns. Include the false-activation
  tests (see `tests/test_completeness_profiles.py`).
- Route/manifest scanners for more frameworks (`analysis/repo_scan.py`).
- Schema improvements (versioned — additive changes only within a major).

## Quality bar

```bash
uv sync
uv run pytest packages/genesis-gates/tests packages/genesis-cli/tests -q
uv run ruff check packages
```
All green, plus tests for anything behavioral. Determinism matters: the audit
must produce the same result twice; no network calls in core analysis.
