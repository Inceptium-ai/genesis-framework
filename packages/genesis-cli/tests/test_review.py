"""AI second-reader: additive-only contract, graceful degradation, CI flag."""

from __future__ import annotations

import json
import subprocess

from genesis_cli import review as review_mod
from genesis_cli.main import main
from genesis_cli.report import render_html, render_terminal
from genesis_cli.review import _sanitize, run_review


def _fake_envelope(payload: dict) -> str:
    return json.dumps({"type": "result", "result": json.dumps(payload)})


GOOD = {
    "executive_summary": "Solid skeleton, weak validation.",
    "additional_findings": [
        {"title": "Stripe webhooks unverified", "severity": "high",
         "area": "security", "detail": "No signature check on /webhooks."},
    ],
    "annotations": [
        {"ref": "secret-scan", "note": "Both hits are in test fixtures — "
                                       "likely false positives (do not remove)."},
    ],
    "remediation_plan": ["Fix webhook verification", "Add CI"],
}


def test_run_review_parses_and_sanitizes(tmp_path, monkeypatch):
    monkeypatch.setattr(review_mod.shutil, "which", lambda _: "/usr/bin/claude")

    def fake_run(cmd, **kwargs):
        assert cmd[0] == "claude" and "-p" in cmd
        assert "ANTHROPIC_API_KEY" not in kwargs["env"]  # subscription auth
        return subprocess.CompletedProcess(cmd, 0, stdout=_fake_envelope(GOOD),
                                           stderr="")

    monkeypatch.setattr(review_mod.subprocess, "run", fake_run)
    out = run_review(tmp_path, {"readiness": {"score": 50}, "gates": []})
    assert out["additive_only"] is True
    assert out["additional_findings"][0]["title"] == "Stripe webhooks unverified"
    assert out["annotations"][0]["ref"] == "secret-scan"


def test_run_review_missing_cli_degrades(tmp_path, monkeypatch):
    monkeypatch.setattr(review_mod.shutil, "which", lambda _: None)
    out = run_review(tmp_path, {})
    assert "error" in out


def test_run_review_garbage_output_degrades(tmp_path, monkeypatch):
    monkeypatch.setattr(review_mod.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        review_mod.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "not json at all", ""))
    out = run_review(tmp_path, {})
    assert "error" in out


def test_sanitize_bounds_and_contract():
    huge = {
        "executive_summary": "x" * 99_999,
        "additional_findings": [{"title": f"f{i}"} for i in range(99)],
        "annotations": [{"ref": "r", "note": "n"}] * 99,
        "remediation_plan": ["s"] * 99,
    }
    out = _sanitize(huge)
    assert len(out["executive_summary"]) == 2000
    assert len(out["additional_findings"]) == 20
    assert len(out["annotations"]) == 20
    assert len(out["remediation_plan"]) == 10
    assert out["additive_only"] is True


def test_renderers_include_review_sections():
    report = {
        "repo": "/x", "generated_at": "now",
        "readiness": {"score": 50, "grade": "F", "categories": []},
        "gates": [], "completeness": {"gaps": []}, "env": {}, "structure": {},
        "ai_review": _sanitize(GOOD),
    }
    term = render_terminal(report)
    assert "AI SECOND-READER" in term and "Stripe webhooks" in term
    html_out = render_html(report)
    assert "AI second-reader" in html_out and "additive-only" in html_out


def _seed_min_repo(tmp_path):
    (tmp_path / "README.md").write_text("# x\n")
    (tmp_path / ".gitignore").write_text("\n")
    return tmp_path


def test_min_score_flag(tmp_path, capsys):
    repo = _seed_min_repo(tmp_path)
    out = tmp_path / "o"
    # Absurdly low bar -> pass regardless of grade.
    assert main(["adopt", str(repo), "--out", str(out), "--min-score", "0"]) == 0
    # Impossible bar -> fail.
    assert main(["adopt", str(repo), "--out", str(out), "--min-score", "101"]) == 1
