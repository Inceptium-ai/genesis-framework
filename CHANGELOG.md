# Changelog

All notable changes to the Genesis Framework are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Both published packages — `genesis-cli` and `genesis-gates` — are versioned and
released together from this repository. A `v*` tag builds and publishes both to
PyPI (see `.github/workflows/release.yml`).

## [Unreleased]

## [0.1.0] — unreleased

First public release. Everything below is new.

### Added

**CLI (`genesis-cli`)**

- `genesis adopt [path]` — audit any repository for agent-readiness in one
  command, with zero API keys and zero infrastructure. Produces a 0–100
  readiness score with a grade, a terminal report, `report.json`,
  `report.html`, `report.sarif`, and an `AGENTS.md` draft written from the
  findings.
  - `--write` / `--force` to install `AGENTS.md` into the repo root.
  - `--json` for the machine-readable report on stdout.
  - `--min-score N` — CI mode: exit 1 below the threshold.
  - `--baseline PATH` + `--fail-on-regression` — diff against a previous
    `report.json` (score delta, grade change, new/resolved findings and
    mandatory-flow gaps) and exit 1 on regression.
  - `--review` — optional AI second-reader through the local `claude` CLI
    using subscription auth (still no API key). Strictly additive: it may add,
    annotate, and prioritize findings, but can never remove or downgrade a
    deterministic finding and never changes the score.
  - `history.jsonl` — every run appends one append-only score record; two or
    more runs render a trend line.
- `genesis init [path]` — scaffold a new or bare repository agent-ready on day
  one: `AGENTS.md`, `.gitignore`, `.env.example`, a self-audit GitHub workflow,
  and a commented `.genesis/config.yaml`. Idempotent — never overwrites.
- `genesis gates [path]` — run just the delivery gates and print a per-gate
  table with the top findings per failed gate. `--gate NAME` (repeatable,
  validated against the registry) and `--json`.
- `genesis --version` — the installed version, single-sourced from package
  metadata.

**Gate engine (`genesis-gates`)**

- Gate/provider registries (`@register_gate`, `@register_provider`) and a
  `GateRunner` that turns a Delivery Manifest plus a repo into a linked,
  exportable Evidence Package.
- Gates: secret-scan, dependency-scan, unit-tests (executed in an isolated venv
  built from the app's own requirements), SAST, and SBOM. Every gate skips
  cleanly with partial credit when its scanner isn't installed.
- Providers for the backing tools, resolved through the provider registry so
  each gate is tool-neutral.

**Analysis**

- **System completeness profiles** — detect the systems a repo implements and
  check their mandatory flows against the implementation surface (routes,
  files) rather than prose. Ships profiles for **auth** (e.g. signup with no
  sign-in), **payments** (e.g. Stripe checkout with no webhook signature
  verification), and **file upload** (e.g. uploads with no size limit).
- **Env reconciliation** — the compose `${VAR}` contract and direct env reads,
  diffed for unset and undocumented variables.
- **Readiness scoring** — 0–100 with a grade, plus **SARIF 2.1.0** output for
  GitHub code scanning or any SARIF viewer.
- **Risk classification** — deterministic: auth, payments, migrations, secrets,
  and infrastructure changes are always high-risk. The substrate for
  bounded-autonomy policies.

**Contracts**

- Versioned JSON Schema (Draft 2020-12) contracts for delivery manifests,
  evidence packages, tasks, gate config, and readiness reports, so any tool can
  produce or consume Genesis artifacts. The schemas ship inside the
  `genesis-gates` wheel; `GENESIS_CONTRACTS_DIR` overrides their location.

**Distribution**

- A composite **GitHub Action** (`packages/genesis-cli/action/`) that installs
  the CLI, runs the audit, uploads SARIF to code scanning, and archives the
  report.
- Both packages published to PyPI via **Trusted Publishing** on a `v*` tag.

[Unreleased]: https://github.com/Inceptium-ai/genesis-framework/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Inceptium-ai/genesis-framework/releases/tag/v0.1.0
