# Genesis Open-Core: How the Split Works

_The maintainer's manual for developing open-source and commercial Genesis in
one place, reliably. Ships in both repos._

## The model in one paragraph

The **private monorepo is the single source of truth for everything**. The
public `genesis-framework` repository is a **verified snapshot** assembled from
an explicit allowlist by `scripts/oss_sync.py`, carrying a
`Genesis-Monorepo-Rev` provenance trailer on every commit. Day-to-day
development does not change: one repo, one test suite, atomic commits that may
span the boundary. Publishing is a deliberate, verified act — never a side
effect.

## The boundary

| Public (OSS, Apache-2.0) | Commercial (private) |
|---|---|
| `packages/genesis-gates` — gate engine, analysis (completeness profiles, env reconciliation, readiness, **risk classification**), JSON Schema contracts | `packages/genesis-server` — orchestration, pipeline, providers, vault, autonomy *policy engine*, GitHub sync |
| `packages/genesis-cli` — `adopt`, `init`, SARIF/JSON reports, AGENTS.md, GitHub Action | `packages/genesis-executor` — coding-agent adapters, sandbox |
| `oss/` staging files (README, CONTRIBUTING, LICENSE, public workspace root) + this doc | `frontend/` — dashboards, TaskBoard, settings |
| | AI second-reader & polished HTML report (held as commercial/consulting tier) |

**The razor** for every new feature: *deterministic analysis, standards, and
local tooling → OSS; orchestration, policy application, UI, and org governance
→ commercial.* When a commercial feature needs a new primitive, the primitive
goes into `genesis-gates` (that's the tithe that keeps the core real).

**The iron rule**: the dependency arrow only points commercial → OSS.
`genesis-gates` and `genesis-cli` never import `genesis_server` or
`genesis_executor`. Enforced by
`packages/genesis-gates/tests/test_oss_boundary.py` (fails the whole suite) and
re-checked on the extracted tree by the sync script.

## The sync (publishing)

```bash
make oss-build            # assemble + VERIFY into build/oss
make oss-publish DIR=~/src/genesis-framework   # + mirror, commit w/ provenance
# push of the public repo is always a manual, human act
```

`scripts/oss_sync.py` stages are all mandatory:
1. **Assemble** — copy ONLY the allowlist (renames `oss/README.md` → `README.md`
   etc.). Anything not listed cannot leak, by construction.
2. **Verify** — gitleaks over the assembled tree; grep re-check of the boundary;
   then `uv sync && pytest` **inside the extracted tree** with its own public
   workspace root — proving the published packages are self-contained.
3. **Publish** (optional) — rsync-mirror into a checkout of the public repo,
   commit with `Genesis-Monorepo-Rev: <sha>`. Pushing is manual.

Cadence: sync on every tagged release (and whenever the public surface changes
meaningfully). PyPI releases are cut from the public repo.

## Versioning & compatibility

- OSS packages use semver; the **JSON Schemas in
  `packages/genesis-gates/contracts/` are the compatibility surface** —
  additive-only within a major version. Commercial code consumes artifacts
  through those schemas, so core evolution can't silently break the platform.
- In monorepo dev, commercial consumes OSS as **workspace source** (no publish
  cycle). Released commercial builds pin published versions, so customers run
  exactly what the community can audit.

## Community contributions (inflow)

PRs are reviewed on the public repo (DCO sign-off, no CLA). Accepted changes
are imported into the monorepo (initially by maintainers applying the patch
with authorship preserved: `git am` / `--author`), land in the normal test
suite, and appear publicly in the next sync. This is documented for
contributors in the public `CONTRIBUTING.md` — no surprises.

## Failure modes & their guards

| Risk | Guard |
|---|---|
| Reverse import sneaks in | boundary test in the default suite + re-check on extraction |
| Secret/IP leaks into public tree | allowlist-only assembly + gitleaks on every build |
| Public packages secretly depend on monorepo layout | standalone test run inside the extracted tree |
| Public drift / mystery provenance | snapshot-only publishing + `Genesis-Monorepo-Rev` trailer |
| Accidental publish | commit ≠ push; push is always manual |

## Graduation criterion

If external contributors ever outpace internal core development, flip to
**public-first**: core development moves to the public repo and the monorepo
consumes releases like any dependency. The snapshot model migrates cleanly to
that world; don't do it before the community exists.
