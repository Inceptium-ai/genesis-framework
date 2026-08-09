"""SARIF report parser.

Normalizes SARIF ``runs[].results[]`` into Genesis ``Finding``s. SARIF is the
common interchange format emitted by CodeQL, Semgrep, many linters, and CI
security steps — so importing a SARIF file is the primary ``defer-to-CI`` path
for SAST-style gates.
"""
from __future__ import annotations

import json

from ..engine.base import Finding, Severity
from ..util import map_severity

# SARIF `level` vocabulary -> Genesis severity (via the shared map).
_LEVEL_DEFAULT = Severity.medium


def parse_sarif(text: str) -> list[Finding]:
    """Parse SARIF text into findings.

    Each ``results[]`` entry becomes one finding: ``ruleId`` is the title, the
    result ``level`` (error/warning/note) maps to severity, the message text is
    the detail, and the first physical location (artifact uri + start line) is
    the location. The gate name is left blank — the gate stamps it.
    """
    data = json.loads(text)
    findings: list[Finding] = []
    for run in data.get("runs") or []:
        for result in run.get("results") or []:
            rule_id = result.get("ruleId") or "unknown-rule"
            level = result.get("level")  # error | warning | note (default: warning)
            message = ""
            msg = result.get("message")
            if isinstance(msg, dict):
                message = msg.get("text", "") or ""
            elif isinstance(msg, str):
                message = msg

            location = None
            for loc in result.get("locations") or []:
                phys = loc.get("physicalLocation") or {}
                uri = (phys.get("artifactLocation") or {}).get("uri")
                if uri:
                    start_line = (phys.get("region") or {}).get("startLine")
                    location = f"{uri}:{start_line}" if start_line is not None else uri
                    break

            findings.append(
                Finding(
                    gate="",
                    title=str(rule_id),
                    severity=map_severity(level, default=_LEVEL_DEFAULT),
                    detail=message,
                    location=location,
                    raw={"ruleId": rule_id, "level": level},
                )
            )
    return findings
