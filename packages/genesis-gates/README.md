# genesis-gates — the Genesis gate engine

The deterministic engine behind [`genesis-cli`](https://pypi.org/project/genesis-cli/):
delivery gates, system-completeness analysis, readiness scoring, risk
classification, and the versioned JSON Schema contracts that make Genesis
artifacts portable between tools.

No API keys, no server, no network calls of its own — gates shell out to
scanners you already have and **skip cleanly** when one isn't installed.

```bash
pip install genesis-gates
```

Most people want the CLI instead: `pip install genesis-cli`.

## What's in it

**Gate engine** (`genesis_gates.engine`) — a `Gate`/`Provider` registry, a
`GateRunner` that turns a Delivery Manifest plus a repo into a linked
`EvidencePackage`, and the `Finding`/`Severity`/`GateStatus` vocabulary every
gate reports in.

**Gates** (`genesis_gates.gates`) — secret-scan, dependency-scan, unit-tests
(run in an isolated venv built from the app's own requirements), SAST, SBOM,
design-lint (a deterministic ban list for known AI-slop UI patterns — gradient
text, default glassmorphism, particle backgrounds — extensible per project via
`.genesis/design-rules.yaml`).

**Analysis** (`genesis_gates.analysis`) — the layer that catches what scanners
can't:

- **System completeness profiles** — detects the systems a repo implements
  (auth, payments, file upload) and checks their *mandatory flows* against the
  implementation surface (routes, files), not prose. This is what catches an
  app that ships signup with no sign-in, Stripe checkout with no webhook
  signature verification, or uploads with no size limit.
- **Env reconciliation** — the compose `${VAR}` contract and direct env reads,
  diffed for unset and undocumented variables.
- **Readiness scoring** — a 0–100 score with a grade, plus SARIF 2.1.0 output.
- **Risk classification** — deterministic: auth, payments, migrations,
  secrets, and infra changes are always high-risk. This is the substrate for
  bounded-autonomy policies ("low-risk changes flow, risky changes wait").

**Contracts** (`genesis_gates/contracts/*.schema.json`) — delivery manifests,
evidence packages, tasks, gate config, and readiness reports as JSON Schema
(Draft 2020-12), so any tool can produce or consume Genesis artifacts. The
schemas ship inside the wheel; `genesis_gates.contracts.contracts_dir()`
resolves them, and `GENESIS_CONTRACTS_DIR` overrides the location.

## Using it directly

```python
from genesis_gates import GateRunner, load_gate_config

runner = GateRunner(config=load_gate_config("."))
package = runner.run(repo=".")
for result in package.results:
    print(result.gate, result.status, len(result.findings))
```

Importing `genesis_gates.gates` and `genesis_gates.providers` populates the
registries via decorator side-effects — which is also how you extend Genesis:
`@register_gate` / `@register_provider` in your own module.

## License

Apache-2.0. Part of the
[Genesis Framework](https://github.com/Inceptium-ai/genesis-framework).
