"""SARIF 2.1.0 renderer — feed `genesis adopt` findings to code-scanning UIs.

One SARIF run per report, one rule per finding source (gate name, plus the
synthetic `completeness` and `env-contract` rules), one result per finding.
GitHub code scanning ingests this directly via `github/codeql-action/upload-sarif`.
"""

from __future__ import annotations

import re
from importlib import metadata

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = ("https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/"
                "schemas/sarif-schema-2.1.0.json")

# Gate/finding severity -> SARIF level.
_LEVELS = {"critical": "error", "high": "error", "medium": "warning",
           "low": "note", "info": "note"}

_RULE_DESCRIPTIONS = {
    "secret-scan": "Hardcoded secrets and credentials detected in the repository.",
    "dependency-scan": "Known-vulnerable dependencies.",
    "sast": "Static-analysis security findings.",
    "unit-tests": "Unit-test failures.",
    "sbom": "Software bill-of-materials findings.",
    "container-scan": "Container image vulnerabilities.",
    "completeness": ("Mandatory system flows that are referenced but not "
                     "implemented (e.g. signup without sign-in)."),
    "env-contract": ("Environment variables referenced with no default and no "
                     "documented value."),
}

# Trailing ":<line>" on a finding location (e.g. "src/app.py:42").
_LINE_SUFFIX = re.compile(r":(\d+)$")


def _tool_version() -> str:
    try:
        return metadata.version("genesis-cli")
    except metadata.PackageNotFoundError:  # running from a source checkout
        return "0.0.0"


def _location(raw: str | None, repo: str) -> list[dict] | None:
    """SARIF locations for a "path[:line]" finding location, repo-relative.

    Returns None when there is no usable file-level location (missing, the
    repo root itself, or an absolute path outside the repo) — SARIF consumers
    want relative URIs or nothing.
    """
    if not raw:
        return None
    line = None
    if m := _LINE_SUFFIX.search(raw):
        line = int(m.group(1))
        raw = raw[: m.start()]
    uri, root = raw, repo.rstrip("/")
    if uri.startswith(root + "/"):
        uri = uri[len(root) + 1:]
    elif uri.startswith("./"):
        uri = uri[2:]
    if not uri or uri == root or uri.startswith("/"):
        return None
    physical: dict = {"artifactLocation": {"uri": uri}}
    if line is not None:
        physical["region"] = {"startLine": line}
    return [{"physicalLocation": physical}]


def _rule(rule_id: str) -> dict:
    rule = {"id": rule_id, "name": rule_id}
    if desc := _RULE_DESCRIPTIONS.get(rule_id):
        rule["shortDescription"] = {"text": desc}
    return rule


def render_sarif(report: dict) -> dict:
    """Build a SARIF 2.1.0 log from a `genesis adopt` report dict."""
    repo = report.get("repo", "")
    results: list[dict] = []
    rule_ids: dict[str, None] = {}  # insertion-ordered set

    # Gate findings (secret-scan, dependency-scan, sast, ...).
    for gate in report.get("gates", []):
        for f in gate.get("findings", []):
            rule_id = f.get("gate") or gate.get("gate") or "genesis"
            rule_ids.setdefault(rule_id)
            message = f.get("title", "")
            if f.get("detail"):
                message = f"{message}: {f['detail']}" if message else f["detail"]
            result = {
                "ruleId": rule_id,
                "level": _LEVELS.get(f.get("severity"), "warning"),
                "message": {"text": message or "(no detail)"},
            }
            if locations := _location(f.get("location"), repo):
                result["locations"] = locations
            results.append(result)

    # Completeness gaps: mandatory system flows with no implementation.
    for gap in report.get("completeness", {}).get("gaps", []):
        rule_ids.setdefault("completeness")
        results.append({
            "ruleId": "completeness",
            "level": "error",
            "message": {"text": f"{gap.get('title', '')}: {gap.get('brief', '')}"},
        })

    # Env contract: required variables with no default and no value.
    for dep in report.get("env", {}).get("missing_required", []):
        rule_ids.setdefault("env-contract")
        var = dep.get("env_var", "?")
        text = (f"{var} is referenced with no default and no documented value; "
                f"supply it before deploying and document it in .env.example.")
        if dep.get("description"):
            text += f" ({dep['description']})"
        results.append({
            "ruleId": "env-contract",
            "level": "warning",
            "message": {"text": text},
        })

    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [{
            "tool": {"driver": {
                "name": "genesis",
                "version": _tool_version(),
                # Matches the $id host used by the project's JSON Schemas.
                "informationUri": "https://genesis.dev",
                "rules": [_rule(r) for r in rule_ids],
            }},
            "results": results,
        }],
    }
