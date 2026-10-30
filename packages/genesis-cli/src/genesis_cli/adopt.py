"""`genesis adopt` — audit any repo for agent-readiness. Deterministic, no LLM.

Pipeline: gates (secrets/deps/tests/sast/sbom, skipping cleanly when tools are
absent) + system-completeness + env reconciliation + structure facts → a scored
readiness report and an AGENTS.md draft.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

# Importing these registers gates/providers via side effects.
import genesis_gates.gates  # noqa: F401
import genesis_gates.providers  # noqa: F401
from genesis_gates import GateContext, GateRunner, get_gate, load_gate_config
from genesis_gates.analysis import (
    analyze,
    declared_env_vars,
    reconcile,
    repo_input,
    score_readiness,
    structure_facts,
)

# Deterministic gate set. risk-classify is excluded on purpose: it consults an
# LLM; `adopt` must run with zero keys and zero infrastructure.
ADOPT_GATES = ("secret-scan", "unit-tests", "dependency-scan", "sast", "sbom")


def run_gates(repo: Path, gate_names: Sequence[str] = ADOPT_GATES) -> dict:
    """Run the named gates over ``repo`` and return the EvidencePackage dict.

    Shared by ``genesis adopt`` (which folds ``gate_results`` into the full
    readiness report) and ``genesis gates`` (which presents the package as-is).
    """
    gates = [get_gate(name)() for name in gate_names]
    manifest = {
        "apiVersion": "genesis.dev/v0",
        "kind": "DeliveryManifest",
        "change": {"id": "ADOPT-0001-001", "title": f"Agent-readiness audit of {repo.name}",
                   "source": "adopted"},
        "service": {"name": repo.name, "owner": "genesis-adopt",
                    "criticality": "medium", "data_classification": "internal"},
        "validation": {"required": [], "coverage_threshold": 0},
        "release": {"risk_level": "medium", "strategy": "direct",
                    "rollback_required": False},
    }
    ctx = GateContext(repo_path=str(repo), manifest=manifest,
                      config=load_gate_config(repo))
    return GateRunner(gates).run(ctx).to_dict()


def run_audit(repo: Path) -> dict:
    """Full audit. Returns the report dict (also consumed by the renderers)."""
    repo = repo.resolve()
    gate_results = run_gates(repo).get("gate_results", [])
    completeness = analyze(repo_input(repo))
    # A brownfield audit has no stored dependency map, so the repo's own
    # `.env.example`-family files ARE its declared config contract. Passing
    # them makes env_hygiene measure "are env vars declared?"; with an empty
    # map, every compose `${VAR}` without a default counted as an undeclared
    # gap even when the repo documents it properly, which pinned the category
    # near zero for any repo with an undefaulted compose reference.
    reconciliation = reconcile(repo, stored_deps=declared_env_vars(repo))
    facts = structure_facts(repo)
    readiness = score_readiness(
        gate_results=gate_results, facts=facts,
        completeness=completeness, reconciliation=reconciliation,
    )
    return {
        "kind": "GenesisReadinessReport",
        "version": 1,
        "repo": str(repo),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "readiness": readiness,
        "gates": gate_results,
        "completeness": completeness,
        "env": reconciliation,
        "structure": facts,
    }
