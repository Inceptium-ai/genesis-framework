"""Shared repo-analysis layer: completeness, reconciliation, readiness.

Used by both the Genesis pipeline (genesis-server) and the standalone
``genesis`` CLI — everything here is deterministic and dependency-light.
"""

from .completeness import (
    AUTH_PROFILE,
    FILE_UPLOAD_PROFILE,
    PAYMENTS_PROFILE,
    PROFILES,
    CompletenessInput,
    MandatoryFlow,
    SystemProfile,
    analyze,
    detect_profiles,
    flow_gaps,
)
from .dependency_reconcile import (
    declared_env_vars,
    reconcile,
    scan_code_vars,
    scan_compose_vars,
)
from .readiness import score_readiness
from .repo_scan import detect_commands, repo_input, structure_facts
from .risk import classify_run, classify_task

__all__ = [
    "AUTH_PROFILE",
    "FILE_UPLOAD_PROFILE",
    "PAYMENTS_PROFILE",
    "PROFILES",
    "CompletenessInput",
    "MandatoryFlow",
    "SystemProfile",
    "analyze",
    "detect_profiles",
    "flow_gaps",
    "classify_run",
    "classify_task",
    "declared_env_vars",
    "reconcile",
    "scan_code_vars",
    "scan_compose_vars",
    "score_readiness",
    "detect_commands",
    "repo_input",
    "structure_facts",
]
