"""System profiles (payments, file_upload) + repo_scan surface semantics.

The auth profile's behavior is pinned by the server suite
(test_system_profiles.py) and the CLI suite (test_adopt.py); this file covers
the pure analysis for the newer profiles and their repo_scan integration.
"""
from __future__ import annotations

import json

from genesis_gates.analysis import (
    FILE_UPLOAD_PROFILE,
    PAYMENTS_PROFILE,
    PROFILES,
    CompletenessInput,
    analyze,
    detect_profiles,
    flow_gaps,
    repo_input,
)


def _inp(marker: str = "", surface: str = "") -> CompletenessInput:
    return CompletenessInput.build(marker, surface)


def test_profiles_registered():
    assert PAYMENTS_PROFILE in PROFILES
    assert FILE_UPLOAD_PROFILE in PROFILES
    assert [p.key for p in PROFILES].count("payments") == 1
    assert [p.key for p in PROFILES].count("file_upload") == 1


# ── payments ────────────────────────────────────────────────────────────────

def test_payments_detected_by_markers_only():
    for marker in ("stripe", "braintree", "paypal", "checkout.session",
                   "payment_intent", "lemonsqueezy"):
        keys = [p.key for p in detect_profiles(_inp(f'"{marker}": "^1.0.0"'))]
        assert keys == ["payments"], marker


def test_payments_all_flows_gap_when_only_checkout_exists():
    inp = _inp('"stripe": "^14.0.0"', "post /api/checkout")
    gaps = {f.key for f in flow_gaps(PAYMENTS_PROFILE, inp)}
    assert gaps == {"webhook_verification", "refund_flow", "idempotency"}


def test_payments_flows_satisfied():
    inp = _inp(
        marker='"stripe": "^14.0.0"\nIDEMPOTENCY_KEY docs',
        surface="post /api/payments/webhook\npost /api/orders/1/refund",
    )
    assert flow_gaps(PAYMENTS_PROFILE, inp) == []


def test_payments_surface_flows_ignore_prose_mentions():
    # "we should verify webhooks and support refunds" in prose is a reference,
    # not an implementation — surface_only flows must still gap.
    inp = _inp('"stripe": "1.0"\nverify webhooks, allow refund', "post /api/pay")
    gaps = {f.key for f in flow_gaps(PAYMENTS_PROFILE, inp)}
    assert {"webhook_verification", "refund_flow"} <= gaps


def test_payments_required_deps_empty_for_v1():
    assert PAYMENTS_PROFILE.required_deps == ()


# ── file_upload ─────────────────────────────────────────────────────────────

def test_file_upload_detected_by_markers():
    for marker in ("multipart/form-data", "python-multipart", "multer",
                   "busboy", "UploadFile", "createReadStream"):
        keys = [p.key for p in detect_profiles(_inp(marker))]
        assert keys == ["file_upload"], marker


def test_file_upload_all_flows_gap_on_bare_upload():
    inp = _inp('"multer": "^1.4.0"', "post /api/files")
    gaps = {f.key for f in flow_gaps(FILE_UPLOAD_PROFILE, inp)}
    assert gaps == {"size_limit", "type_validation", "delete_flow"}


def test_file_upload_flows_satisfied():
    inp = _inp(
        marker="multipart/form-data upload, PDF or DOCX only, up to 10MB; "
               "415 unsupported file format",
        surface="post /api/files\ndelete /api/files/{id}",
    )
    assert flow_gaps(FILE_UPLOAD_PROFILE, inp) == []


def test_size_limit_evidence_variants():
    assert "size_limit" in {f.key for f in
                            flow_gaps(FILE_UPLOAD_PROFILE, _inp("multer"))}
    for text in ("MAX_FILE_SIZE=10485760", "maxFileSize: 5242880",
                 "MAX_UPLOAD_SIZE", "content-length limit", "up to 25 MB"):
        gaps = {f.key for f in
                flow_gaps(FILE_UPLOAD_PROFILE, _inp(f"multer\n{text}"))}
        assert "size_limit" not in gaps, text


# ── cross-profile hygiene ───────────────────────────────────────────────────

def test_auth_fixture_does_not_activate_new_profiles(tmp_path):
    """The canonical auth seed (next-auth app) must stay auth-only."""
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "demo", "scripts": {"test": "vitest"},
        "dependencies": {"next-auth": "^4.0.0"},
    }))
    src = tmp_path / "src" / "pages" / "api"
    src.mkdir(parents=True)
    (src / "signup.ts").write_text("router.post('/api/auth/signup', signup)\n")
    assert analyze(repo_input(tmp_path))["profiles"] == ["auth"]


def test_repo_scan_surface_includes_http_methods(tmp_path):
    """A DELETE route must satisfy delete_flow even when the URL itself
    contains no 'delete' — the method is part of the surface."""
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "files", "dependencies": {"multer": "^1.4.0"},
    }))
    (tmp_path / ".env.example").write_text("MAX_FILE_SIZE=10485760\n"
                                           "ALLOWED_FILE_TYPES=pdf,docx\n")
    src = tmp_path / "src"
    src.mkdir()
    (src / "files.js").write_text(
        "app.post('/api/files', upload);\n"
        "app.delete('/api/files/:id', remove);\n"
    )
    report = analyze(repo_input(tmp_path))
    assert report["profiles"] == ["file_upload"]
    assert report["gaps"] == []


def test_repo_scan_reads_openapi_as_marker_source(tmp_path):
    """An API contract at the repo root is a marker source (like the
    pipeline's planning artifacts): multipart + limits described there count."""
    (tmp_path / "openapi.yaml").write_text(
        "openapi: 3.0.3\n"
        "paths:\n"
        "  /api/v1/files:\n"
        "    post:\n"
        "      summary: Upload a PDF (10MB limit) via multipart/form-data\n"
        "      responses:\n"
        "        '415':\n"
        "          description: Unsupported file format\n"
    )
    report = analyze(repo_input(tmp_path))
    assert report["profiles"] == ["file_upload"]
    gap_flows = {g["flow"] for g in report["gaps"]}
    assert "size_limit" not in gap_flows
    assert "type_validation" not in gap_flows
    assert "delete_flow" in gap_flows  # no DELETE route anywhere
