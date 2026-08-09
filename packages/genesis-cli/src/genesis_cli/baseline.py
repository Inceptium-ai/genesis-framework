"""Baseline comparison + score history — the CI regression story.

`compare` diffs two `genesis adopt` reports. Identity is deliberately simple
and stable across runs: a gate finding is ``(gate, title, location)``, a
completeness gap is ``(profile, flow)``. ``regressed`` is the CI signal: the
score dropped by more than half a point, a mandatory-flow gap appeared, or a
new critical/high finding appeared.

`append_history` keeps one JSON line per audit in ``<out_dir>/history.jsonl``
(append-only — never rewritten), so the readiness score becomes a trackable
time series without any infrastructure.
"""

from __future__ import annotations

import json
from pathlib import Path

# A dip within this tolerance is jitter (e.g. a gate flipping skipped→passed),
# not a regression.
SCORE_TOLERANCE = 0.5

_REGRESSION_SEVERITIES = frozenset({"critical", "high"})

HISTORY_FILE = "history.jsonl"


def _findings_by_identity(report: dict) -> dict[tuple, dict]:
    """Gate findings keyed by (gate, title, location); first occurrence wins."""
    out: dict[tuple, dict] = {}
    for gate in report.get("gates", []):
        for f in gate.get("findings", []):
            key = (f.get("gate") or gate.get("gate"), f.get("title"),
                   f.get("location"))
            out.setdefault(key, f)
    return out


def _gaps_by_identity(report: dict) -> dict[tuple, dict]:
    """Completeness gaps keyed by (profile, flow)."""
    return {(g.get("profile"), g.get("flow")): g
            for g in report.get("completeness", {}).get("gaps", [])}


def compare(current: dict, baseline: dict) -> dict:
    """Diff ``current`` against ``baseline`` (two adopt report dicts)."""
    cur_f, base_f = _findings_by_identity(current), _findings_by_identity(baseline)
    cur_g, base_g = _gaps_by_identity(current), _gaps_by_identity(baseline)

    score_delta = round(
        current["readiness"]["score"] - baseline["readiness"]["score"], 1)
    cur_grade = current["readiness"]["grade"]
    base_grade = baseline["readiness"]["grade"]
    grade_change = f"{base_grade}→{cur_grade}" if cur_grade != base_grade else None

    new_findings = [f for k, f in cur_f.items() if k not in base_f]
    resolved_findings = [f for k, f in base_f.items() if k not in cur_f]
    new_gaps = [g for k, g in cur_g.items() if k not in base_g]
    resolved_gaps = [g for k, g in base_g.items() if k not in cur_g]

    regressed = (
        score_delta < -SCORE_TOLERANCE
        or bool(new_gaps)
        or any(f.get("severity") in _REGRESSION_SEVERITIES for f in new_findings)
    )
    return {
        "score_delta": score_delta,
        "grade_change": grade_change,
        "new_findings": new_findings,
        "resolved_findings": resolved_findings,
        "new_gaps": new_gaps,
        "resolved_gaps": resolved_gaps,
        "regressed": regressed,
    }


def append_history(out_dir: Path, report: dict) -> list[dict]:
    """Append this audit to ``<out_dir>/history.jsonl``; return all entries.

    Append-only by design: the file is the artifact directory's score ledger.
    Unparseable lines (e.g. from a truncated write) are skipped, not fatal.
    """
    entry = {
        "generated_at": report["generated_at"],
        "score": report["readiness"]["score"],
        "grade": report["readiness"]["grade"],
        "findings_count": sum(len(g.get("findings", []))
                              for g in report.get("gates", [])),
        "gaps_count": len(report.get("completeness", {}).get("gaps", [])),
    }
    path = out_dir / HISTORY_FILE
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")

    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def render_trend(entries: list[dict], max_shown: int = 8) -> str | None:
    """One-line score trend ("history: 44.8 → 49.0 → 75.5 (3 runs)").

    None when there is nothing to compare yet (fewer than two runs).
    """
    if len(entries) < 2:
        return None
    shown = entries[-max_shown:]
    scores = " → ".join(str(e["score"]) for e in shown)
    prefix = "… → " if len(entries) > len(shown) else ""
    return f"history: {prefix}{scores} ({len(entries)} runs)"
