"""AI second-reader for `genesis adopt --review`.

The deterministic audit is the trust anchor; this pass is **additive-only**:
it may add findings, annotate, prioritize, and summarize — it can never remove
or downgrade a deterministic finding, and it never changes the readiness score.

Runs through the local `claude` CLI in headless mode using the user's existing
subscription login (ANTHROPIC_API_KEY is explicitly unset in the child), so
`--review` keeps the audit's zero-API-key property. If the CLI is unavailable
or the call fails, the audit proceeds without the review — degraded, never
broken.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

_TIMEOUT_S = 240
_MAX_TREE_ENTRIES = 300
_SKIP_DIRS = {".git", "node_modules", "dist", "build", "__pycache__", ".venv",
              "venv", ".next", "coverage", ".genesis-work", ".genesis-report"}

_INSTRUCTIONS = """\
You are the AI second-reader for a Genesis agent-readiness audit. A
deterministic scanner already produced the findings in REPORT_JSON for the
repository whose file tree is in REPO_TREE.

Your job is ADDITIVE ONLY: find what the deterministic pass missed, annotate
what it found, and write the executive summary. You must NOT dispute scores or
suggest removing findings — if you believe a finding is a false positive, add
an annotation saying so; never omit it.

Look especially for:
- system kinds the scanner has no profile for (payments, file upload, email,
  multi-tenancy, background jobs) and mandatory flows they imply that appear
  unimplemented;
- architectural risks visible from the tree/report (no service boundaries,
  test gaps in critical areas, framework misuse);
- anything that would make an AI coding agent unsafe or unproductive in this
  repo (missing conventions, ambiguous structure, dead config).

Respond with ONLY a JSON object, no prose before or after, exactly this shape:
{
  "executive_summary": "3-6 sentences a CTO reads first",
  "additional_findings": [
    {"title": "...", "severity": "critical|high|medium|low",
     "area": "completeness|security|architecture|testing|agent-readiness",
     "detail": "1-3 sentences, concrete"}
  ],
  "annotations": [
    {"ref": "which deterministic finding/category this refers to",
     "note": "context, likely-false-positive flag, or prioritization"}
  ],
  "remediation_plan": ["ordered, concrete first 5-8 steps"]
}
"""


def claude_available() -> bool:
    return shutil.which("claude") is not None


def _repo_tree(repo: Path) -> str:
    lines: list[str] = []
    for f in sorted(repo.rglob("*")):
        if len(lines) >= _MAX_TREE_ENTRIES:
            lines.append("… (tree truncated)")
            break
        if any(part in _SKIP_DIRS for part in f.parts):
            continue
        if f.is_file():
            lines.append(str(f.relative_to(repo)))
    return "\n".join(lines)


def _extract_json(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _sanitize(data: dict) -> dict:
    """Enforce the additive-only contract and bound sizes."""
    findings = []
    for f in (data.get("additional_findings") or [])[:20]:
        if isinstance(f, dict) and f.get("title"):
            findings.append({
                "title": str(f.get("title"))[:200],
                "severity": str(f.get("severity", "medium")).lower(),
                "area": str(f.get("area", "architecture"))[:40],
                "detail": str(f.get("detail", ""))[:600],
            })
    annotations = [
        {"ref": str(a.get("ref", ""))[:200], "note": str(a.get("note", ""))[:400]}
        for a in (data.get("annotations") or [])[:20]
        if isinstance(a, dict) and a.get("note")
    ]
    plan = [str(s)[:300] for s in (data.get("remediation_plan") or [])[:10]]
    return {
        "executive_summary": str(data.get("executive_summary", ""))[:2000],
        "additional_findings": findings,
        "annotations": annotations,
        "remediation_plan": plan,
        "additive_only": True,
        "engine": "claude-cli (subscription)",
    }


def run_review(repo: Path, report: dict) -> dict:
    """Return the ai_review block, or {"error": ...} (audit proceeds either way)."""
    if not claude_available():
        return {"error": "claude CLI not found on PATH — install Claude Code or "
                         "run without --review"}
    slim = {k: report[k] for k in ("readiness", "gates", "completeness", "env",
                                   "structure") if k in report}
    prompt = (
        f"{_INSTRUCTIONS}\n\nREPO_TREE:\n{_repo_tree(repo)}\n\n"
        f"REPORT_JSON:\n{json.dumps(slim, indent=1)[:60_000]}\n"
    )
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # force subscription auth
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json"],
            capture_output=True, text=True, timeout=_TIMEOUT_S, env=env,
            cwd=str(repo),
        )
    except subprocess.TimeoutExpired:
        return {"error": f"AI review timed out after {_TIMEOUT_S}s"}
    except OSError as exc:
        return {"error": f"could not launch claude CLI: {exc}"}
    if proc.returncode != 0:
        return {"error": f"claude CLI exited {proc.returncode}: "
                         f"{(proc.stderr or proc.stdout)[:300]}"}

    # --output-format json wraps the answer in an envelope with a `result` field.
    envelope = _extract_json(proc.stdout) or {}
    payload = envelope.get("result", proc.stdout)
    data = _extract_json(payload if isinstance(payload, str) else "")
    if data is None:
        return {"error": "AI review returned unparseable output"}
    return _sanitize(data)
