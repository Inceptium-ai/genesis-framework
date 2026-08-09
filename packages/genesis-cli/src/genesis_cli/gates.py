"""`genesis gates` — run just the deterministic delivery gates, no full audit.

Same gate set and configuration as `genesis adopt` (via `adopt.run_gates`),
but without the completeness/env/structure analysis: a fast per-gate table for
local iteration ("did my fix turn secret-scan green?") and scripting.
"""

from __future__ import annotations

_STATUS_MARKS = {"passed": "✓", "failed": "✗", "skipped": "–", "error": "!"}


def render_gates_terminal(package: dict) -> str:
    """Per-gate table (name, status, summary) + findings for failed gates."""
    results = package.get("gate_results", [])
    if not results:
        return "no gates were run"
    name_w = max(len("GATE"), *(len(str(r.get("gate", ""))) for r in results))

    lines = ["", f"  {'GATE':<{name_w}}  {'STATUS':<8}  SUMMARY"]
    for r in results:
        mark = _STATUS_MARKS.get(r.get("status"), "?")
        summary = r.get("summary") or r.get("error") or ""
        lines.append(f"{mark} {r.get('gate', ''):<{name_w}}  "
                     f"{r.get('status', ''):<8}  {summary[:80]}")

    for r in results:
        findings = r.get("findings", [])
        if r.get("status") != "failed" or not findings:
            continue
        lines += ["", f"── {r['gate']}: top findings " +
                  "─" * max(0, 50 - len(str(r["gate"])))]
        for f in findings[:5]:
            lines.append(f"   [{f.get('severity')}] {f.get('title')}")
            if f.get("location"):
                lines.append(f"     at {f['location']}")
            if f.get("remediation"):
                lines.append(f"     fix: {f['remediation']}")
        if len(findings) > 5:
            lines.append(f"   … {len(findings) - 5} more (use --json for all)")

    lines.append("")
    return "\n".join(lines)


def gates_exit_code(package: dict) -> int:
    """0 when every selected gate passed or skipped, 1 otherwise."""
    ok = all(r.get("status") in ("passed", "skipped")
             for r in package.get("gate_results", []))
    return 0 if ok else 1
