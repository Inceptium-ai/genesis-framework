"""Shared pytest fixtures + paths for the genesis-gates test suite.

The control-plane (server) is not vendored into this package, so the SQLite
pinning and the FastAPI ``client`` fixture from the upstream toolkit are omitted.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# --- Repo layout ----------------------------------------------------------- #
REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples"
SAMPLE_MANIFEST = EXAMPLES / "manifest.sample.yaml"
INVALID_MANIFEST = EXAMPLES / "manifest.invalid.yaml"
SAMPLE_REPO = EXAMPLES / "sample-repo"


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


@pytest.fixture
def sample_manifest() -> dict:
    return load_yaml(SAMPLE_MANIFEST)


@pytest.fixture
def invalid_manifest() -> dict:
    return load_yaml(INVALID_MANIFEST)


def make_evidence(status: str = "passed", severity: str = "high") -> dict:
    """A minimal but schema-valid Evidence Package with one finding."""
    finding = {
        "gate": "unit-tests",
        "title": "example finding",
        "severity": severity,
        "detail": "an example finding for tests",
        "location": "app.py:1",
        "remediation": None,
    }
    return {
        "apiVersion": "genesis.dev/v0",
        "kind": "EvidencePackage",
        "change_id": "CHG-2026-00421",
        "service": "document-api",
        "owner": "customer-platform",
        "risk_level": "medium",
        "status": status,
        "created_at": "2026-07-10T12:00:00+00:00",
        "manifest": {
            "change": {"id": "CHG-2026-00421", "title": "t", "source": "ai-assisted"},
            "service": {
                "name": "document-api",
                "owner": "customer-platform",
                "criticality": "high",
                "data_classification": "confidential",
            },
            "release": {"risk_level": "medium"},
        },
        "gate_results": [
            {
                "gate": "unit-tests",
                "status": status,
                "summary": "1 passed",
                "duration_s": 0.01,
                "error": None,
                "findings": [finding],
            }
        ],
        "findings": [finding],
    }


@pytest.fixture
def valid_evidence() -> dict:
    return make_evidence()
