# Genesis Readiness Audit — GitHub Action

Composite action wrapping `genesis adopt`: audits the checked-out repository for
agent-readiness (delivery gates, system completeness, env hygiene, structure),
uploads the SARIF to GitHub code scanning, and always attaches the full
`.genesis-report` (HTML, JSON, SARIF, AGENTS.md draft) as a build artifact.
Deterministic — no API keys, no infrastructure.

## Usage

```yaml
name: Genesis readiness
on: [push, pull_request]

permissions:
  contents: read
  security-events: write   # for the SARIF upload

jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: <owner>/<repo>/packages/genesis-cli/action@main
        with:
          path: .            # repo or subdirectory to audit (default: .)
          min-score: "70"    # fail below this readiness score (default: "0")
          upload-sarif: "true"
```

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `path` | `.` | Path of the repository (or subdirectory) to audit. |
| `min-score` | `"0"` | Fail the job when the readiness score is below this threshold; `"0"` reports without failing. |
| `upload-sarif` | `"true"` | Upload `report.sarif` to GitHub code scanning. |

## Outputs

No formal outputs; the audit publishes:

- **Code scanning alerts** from `report.sarif` (category `genesis-readiness`).
- A **`genesis-report` artifact** containing `report.html`, `report.json`,
  `report.sarif`, and the drafted `AGENTS.md` — uploaded even when the
  `min-score` gate fails the job.
