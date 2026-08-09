"""Direct tests for the report/output parsers (SARIF, JUnit, CycloneDX).

Parsers take file text and return ``list[Finding]`` with the gate name left
blank (the gate stamps it). We exercise them on the example fixtures + inline
strings, independent of any provider or gate.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from genesis_gates.engine.base import Severity
from genesis_gates.parsers import (
    PARSERS,
    UNSUPPORTED_PARSERS,
    parse_cyclonedx,
    parse_junit,
    parse_sarif,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORTS = REPO_ROOT / "examples" / "reports"


def test_parse_sarif_yields_two_findings_of_differing_severity() -> None:
    findings = parse_sarif((REPORTS / "sample.sarif").read_text())
    assert len(findings) == 2

    by_title = {f.title: f for f in findings}
    assert by_title["py/sql-injection"].severity is Severity.high  # level=error
    assert by_title["py/weak-hash"].severity is Severity.medium  # level=warning

    inj = by_title["py/sql-injection"]
    assert inj.location == "app/db.py:42"
    assert "SQL injection" in inj.detail
    assert inj.gate == ""  # left blank for the gate to stamp


def test_parse_sarif_note_maps_to_info() -> None:
    text = """
    {"runs": [{"results": [
      {"ruleId": "r1", "level": "note", "message": {"text": "fyi"},
       "locations": [{"physicalLocation": {"artifactLocation": {"uri": "a.py"}}}]}
    ]}]}
    """
    findings = parse_sarif(text)
    assert len(findings) == 1
    assert findings[0].severity is Severity.info
    assert findings[0].location == "a.py"  # no startLine -> uri only


def test_parse_junit_reports_failures_only() -> None:
    findings = parse_junit((REPORTS / "junit.xml").read_text())
    assert len(findings) == 1  # 2 passing cases ignored, 1 failure kept
    f = findings[0]
    assert f.severity is Severity.high
    assert f.title == "app.tests.test_auth.test_login_rejects_bad_password"
    assert "expected 401" in f.detail


def test_parse_junit_counts_errors_too() -> None:
    text = """<testsuite name="s">
      <testcase classname="c" name="ok"/>
      <testcase classname="c" name="boom"><error message="kaboom"/></testcase>
    </testsuite>"""
    findings = parse_junit(text)
    assert len(findings) == 1
    assert findings[0].title == "c.boom"
    assert findings[0].detail == "kaboom"


def test_parse_cyclonedx_counts_components() -> None:
    text = '{"bomFormat": "CycloneDX", "components": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}'
    findings = parse_cyclonedx(text)
    assert len(findings) == 1
    assert findings[0].severity is Severity.info
    assert findings[0].title == "SBOM: 3 components"
    assert findings[0].raw["component_count"] == 3


def test_parse_cyclonedx_supports_spdx_packages() -> None:
    text = '{"spdxVersion": "SPDX-2.3", "packages": [{"name": "x"}, {"name": "y"}]}'
    findings = parse_cyclonedx(text)
    assert findings[0].title == "SBOM: 2 components"


def test_parsers_dict_wires_all_three() -> None:
    assert set(PARSERS) == {"sarif", "junit", "cyclonedx"}
    assert UNSUPPORTED_PARSERS == {
        "trivy-json",
        "gitleaks-json",
        "syft-json",
        "pip-audit-json",
    }


def test_parse_sarif_bad_json_raises() -> None:
    # json.loads raises JSONDecodeError, a ValueError subclass.
    with pytest.raises(ValueError):
        parse_sarif("not json")
