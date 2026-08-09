"""Agent-readiness scoring — one number a CTO can track, with receipts.

Combines four categories into a 0–100 score + grade:

- validation (40): did the delivery gates pass (tests, secrets, deps, sast)?
- structure  (30): is the repo agent-ready (tests, CI, AGENTS.md, lockfile,
  .env.example, README, containerization)?
- completeness (20): mandatory system flows implemented (no "signup without
  sign-in").
- env_hygiene (10): deploy env contract matches what the code reads.

The score is deliberately harsh on missing tests/secrets hygiene — those are
the two failure modes that make AI-generated changes unshippable.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CategoryScore:
    key: str
    score: float          # 0..1
    weight: int
    details: list[str] = field(default_factory=list)

    @property
    def points(self) -> float:
        return round(self.score * self.weight, 1)


_GRADES = ((90, "A"), (80, "B"), (70, "C"), (55, "D"), (0, "F"))


def _gate_score(gate_results: list[dict]) -> CategoryScore:
    details: list[str] = []
    if not gate_results:
        return CategoryScore("validation", 0.0, 40, ["no gates were run"])
    weights = {"unit-tests": 3, "secret-scan": 3, "dependency-scan": 2,
               "sast": 1, "sbom": 1, "container-scan": 1, "risk-classify": 0}
    earned = total = 0.0
    for gr in gate_results:
        name, status = gr.get("gate"), gr.get("status")
        w = weights.get(name, 1)
        if w == 0:
            continue
        total += w
        if status == "passed":
            earned += w
            details.append(f"{name}: passed")
        elif status == "skipped":
            earned += w * 0.4  # not run is better than failed, worse than green
            details.append(f"{name}: skipped ({gr.get('summary', '')[:60]})")
        else:
            details.append(f"{name}: {status} ({gr.get('summary', '')[:60]})")
    return CategoryScore("validation", earned / total if total else 0.0, 40, details)


def _structure_score(facts: dict) -> CategoryScore:
    checks = (
        ("has_tests", 3, "test suite present"),
        ("has_ci", 2, "CI pipeline configured"),
        ("has_agents_md", 2, "agent instructions (AGENTS.md/CLAUDE.md/.cursorrules)"),
        ("has_lockfile", 1, "dependency lockfile"),
        ("has_env_example", 1, ".env.example documents configuration"),
        ("has_readme", 1, "README"),
        ("has_dockerfile", 1, "containerized"),
        ("has_gitignore", 1, ".gitignore"),
    )
    earned = total = 0.0
    details = []
    for key, w, label in checks:
        total += w
        if facts.get(key):
            earned += w
            details.append(f"✓ {label}")
        else:
            details.append(f"✗ missing: {label}")
    return CategoryScore("structure", earned / total, 30, details)


def _completeness_score(completeness: dict) -> CategoryScore:
    profiles = completeness.get("profiles", [])
    gaps = completeness.get("gaps", [])
    if not profiles:
        return CategoryScore("completeness", 1.0, 20,
                             ["no system profiles detected (nothing to check)"])
    # Each gap costs; three gaps in one profile ~ zero credit.
    score = max(0.0, 1.0 - len(gaps) / 3.0)
    details = [f"profiles detected: {', '.join(profiles)}"]
    details += [f"✗ missing mandatory flow: {g['title']}" for g in gaps]
    if not gaps:
        details.append("✓ all mandatory flows implemented")
    return CategoryScore("completeness", score, 20, details)


def _env_score(recon: dict) -> CategoryScore:
    missing = recon.get("missing_required", [])
    code_only = recon.get("code_only", [])
    score = 1.0
    details = []
    if missing:
        score -= min(0.7, 0.2 * len(missing))
        details += [f"✗ env var referenced with no default and no value: "
                    f"{d['env_var']}" for d in missing[:6]]
    if code_only:
        score -= min(0.3, 0.05 * len(code_only))
        details.append(f"code reads env vars outside the deploy contract: "
                       f"{', '.join(code_only[:6])}")
    if not details:
        details.append("✓ deploy env contract matches code")
    return CategoryScore("env_hygiene", max(0.0, score), 10, details)


def score_readiness(*, gate_results: list[dict], facts: dict,
                    completeness: dict, reconciliation: dict) -> dict:
    cats = [
        _gate_score(gate_results),
        _structure_score(facts),
        _completeness_score(completeness),
        _env_score(reconciliation),
    ]
    total = round(sum(c.points for c in cats), 1)
    grade = next(g for cutoff, g in _GRADES if total >= cutoff)
    return {
        "score": total,
        "grade": grade,
        "categories": [
            {"key": c.key, "points": c.points, "max": c.weight,
             "details": c.details}
            for c in cats
        ],
    }
