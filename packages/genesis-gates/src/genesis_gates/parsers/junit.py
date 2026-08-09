"""JUnit XML report parser.

Normalizes a JUnit ``<testsuite>``/``<testcase>`` report into findings: each
failing or erroring test case becomes one ``high`` finding. Passing and skipped
cases are ignored (they are not findings). This is the ``defer-to-CI`` path for
test gates that already run in someone else's pipeline.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from ..engine.base import Finding, Severity


def parse_junit(text: str) -> list[Finding]:
    """Parse JUnit XML text into findings (one per failing/erroring testcase)."""
    root = ET.fromstring(text)
    # The root may be a single <testsuite> or a <testsuites> wrapper.
    findings: list[Finding] = []
    for testcase in root.iter("testcase"):
        problem = testcase.find("failure")
        kind = "failure"
        if problem is None:
            problem = testcase.find("error")
            kind = "error"
        if problem is None:
            continue  # passed or skipped -> not a finding

        classname = testcase.get("classname", "")
        name = testcase.get("name", "test")
        title = f"{classname}.{name}" if classname else name
        detail = problem.get("message") or (problem.text or "").strip()

        findings.append(
            Finding(
                gate="",
                title=title,
                severity=Severity.high,
                detail=detail,
                location=classname or None,
                raw={"kind": kind, "type": problem.get("type")},
            )
        )
    return findings
