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

## You do not have to contribute to extend Genesis

Gates and providers are discoverable from *any* installed distribution via
entry points — publish your own package instead of forking this one:

```toml
# your-plugin/pyproject.toml
[project.entry-points."genesis_gates.gates"]
license-check = "my_plugin.gates.license_check:LicenseCheckGate"

[project.entry-points."genesis_gates.providers"]
my-license-scanner = "my_plugin.providers.my_license_scanner:MyLicenseScanner"
```

Each entry point resolves either to a `Gate`/`Provider` subclass (registered
under its `name`/`key`) or to a module that self-registers on import with
`@register_gate` / `@register_provider`. Install it next to Genesis and the gate
shows up in `genesis gates`, in `.genesis/config.yaml`, and in manifests like
any built-in. Two rules to know:

- **Fail-soft, but visible.** A plugin that raises on load cannot break the CLI;
  the failure is collected in `genesis_gates.plugin_errors` and printed. A
  plugin that loads but registers nothing is reported too.
- **Built-ins win — registration is append-only.** Plugins load after the
  built-ins and may add names, never take over one that exists. An installed
  package must not be able to re-register `secret-scan` as a no-op and weaken
  every audit on the machine. Extend the check set; don't redefine it.

Whether in-tree or in a plugin, a gate must be **deterministic** (same repo in,
same result out; no network in analysis), **fail-closed on its own errors**
(crash → `error`, missing tool → `skipped`, never a silent pass), and must
**never raise on findings** — blocking is the manifest's decision
(`validation.required` vs `advisory`), not the gate's. Full write-up:
[`docs/architecture.md` → Extending Genesis](docs/architecture.md).

## Quality bar

```bash
uv sync
uv run pytest packages/genesis-gates/tests packages/genesis-cli/tests -q
uv run ruff check packages
```
All green, plus tests for anything behavioral. Determinism matters: the audit
must produce the same result twice; no network calls in core analysis.
