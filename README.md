# Genesis Framework

**An open framework for making AI-generated software reliable, testable,
traceable, and portable — across models, coding agents, clouds, and DevOps
tools.**

AI coding agents write more of the world's software every month, while trust in
their output falls. The missing layer isn't a better agent — it's a delivery
framework that makes agent work *provable*. Genesis provides that layer:

- **`genesis adopt <repo>`** — audit any codebase for agent-readiness in one
  command, with **zero API keys and zero infrastructure**: delivery gates
  (secrets, dependencies, tests-in-isolated-env, SAST, SBOM), **system
  completeness checks** that catch what scanners can't (an app that ships
  signup with no sign-in; Stripe checkout with no webhook verification;
  uploads with no size limit), env-contract reconciliation, and a 0–100
  readiness score. Outputs: terminal report, `report.json`,
  **SARIF 2.1.0** (feed GitHub code scanning), and an `AGENTS.md` your AI
  tools can follow.
- **`genesis init`** — scaffold a new repo agent-ready on day one.
- **Deterministic risk classification** — auth/payments/migrations/secrets/
  infra changes are always high-risk; the substrate for bounded-autonomy
  policies ("low-risk changes flow, risky changes wait for a human").
- **Versioned schemas** — delivery manifests, evidence packages, tasks, and
  readiness reports as JSON Schema contracts (`packages/genesis-gates/contracts/`),
  so any tool can produce or consume Genesis artifacts.
- **A composite GitHub Action** for CI (`packages/genesis-cli/action/`).

```bash
uv sync                       # or: pip install -e packages/genesis-cli
uv run genesis adopt .        # audit the current repo
uv run genesis init my-app    # start a new repo agent-ready
```

## What this repo is (and isn't)

This is the **framework**: schemas, gates, analysis, and local tooling —
Apache-2.0, tool-neutral, and designed to be extended (add gates, providers,
and system profiles). The commercial Genesis platform (orchestration service,
dashboards, organization-wide governance, managed infrastructure) builds **on
top of** these packages and is not part of this repository. The dependency only
ever points that way: this framework imports nothing from the platform.

This repository is a verified snapshot published from the Genesis monorepo;
every commit carries a `Genesis-Monorepo-Rev` provenance trailer. Issues and
pull requests are welcome here — see [CONTRIBUTING.md](CONTRIBUTING.md).

## Packages

| Package | What it is |
|---|---|
| `packages/genesis-gates` | Gate engine (secret-scan, dependency-scan, unit-tests, SAST, SBOM…), analysis layer (system-completeness profiles, env reconciliation, readiness scoring, risk classification), JSON Schema contracts |
| `packages/genesis-cli` | The `genesis` command: `adopt`, `init`; SARIF/JSON/HTML reports; AGENTS.md generation; the GitHub Action |

## License

Apache-2.0 — see [LICENSE](LICENSE). "Genesis" naming and marks are reserved.
