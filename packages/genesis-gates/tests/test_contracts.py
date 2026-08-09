"""Contract (JSON Schema) validation: manifest + evidence."""
from __future__ import annotations

import copy

from genesis_gates.contracts import validate_evidence, validate_manifest

from .conftest import make_evidence


def test_sample_manifest_is_valid(sample_manifest):
    assert validate_manifest(sample_manifest) == []


def test_invalid_manifest_reports_expected_paths(invalid_manifest):
    errors = validate_manifest(invalid_manifest)
    # Exactly the four planted problems, identified by their offending paths.
    locations = {e.split(": ", 1)[0] for e in errors}
    assert locations == {
        "change/id",
        "change/source",
        "service/criticality",
        "validation/required/1",
    }
    assert len(errors) == 4


def test_validate_evidence_accepts_good_package(valid_evidence):
    assert validate_evidence(valid_evidence) == []


def test_validate_evidence_rejects_malformed_package():
    bad = make_evidence()
    # Two independent violations: missing required field + bad enum value.
    del bad["status"]
    bad["apiVersion"] = "wrong/version"
    errors = validate_evidence(bad)
    assert errors  # non-empty -> rejected
    joined = " ".join(errors)
    assert "status" in joined
    assert "apiVersion" in joined


def test_validate_evidence_rejects_bad_finding_severity(valid_evidence):
    bad = copy.deepcopy(valid_evidence)
    bad["findings"][0]["severity"] = "catastrophic"  # not in the enum
    assert validate_evidence(bad)
