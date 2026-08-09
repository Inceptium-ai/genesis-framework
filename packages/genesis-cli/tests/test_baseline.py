"""Baseline comparison: compare() matrix, --baseline / --fail-on-regression
CLI behavior, and the append-only score history."""

from __future__ import annotations

import json

from genesis_cli.baseline import append_history, compare, render_trend
from genesis_cli.main import main

# ── fixtures ────────────────────────────────────────────────────────────────


def _report(score: float, grade: str, findings=(), gaps=()) -> dict:
    """Minimal report dict with the fields compare() consumes."""
    return {
        "kind": "GenesisReadinessReport",
        "repo": "/tmp/demo",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "readiness": {"score": score, "grade": grade, "categories": []},
        "gates": [{"gate": "secret-scan", "status": "passed", "summary": "",
                   "findings": list(findings)}],
        "completeness": {"profiles": ["auth"], "gaps": list(gaps)},
        "env": {"missing_required": []},
        "structure": {},
    }


def _finding(title: str, severity: str = "high",
             location: str | None = "src/app.py:3") -> dict:
    return {"gate": "secret-scan", "title": title, "severity": severity,
            "detail": "", "location": location, "remediation": "rotate it"}


_GAP = {"profile": "auth", "flow": "sign_in",
        "title": "Implement returning-user sign-in (login)", "brief": ""}


def _seed_repo(tmp_path):
    """Same synthetic repo as test_adopt: auth profile with a sign_in gap."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "demo", "scripts": {"test": "vitest", "build": "vite build"},
        "dependencies": {"next-auth": "^4.0.0"},
    }))
    src = tmp_path / "src" / "pages" / "api"
    src.mkdir(parents=True)
    (src / "signup.ts").write_text(
        "export default handler\n// POST /api/signup\n"
        "router.post('/api/auth/signup', signup)\n"
    )
    (tmp_path / "README.md").write_text("# demo\n")
    (tmp_path / ".gitignore").write_text("node_modules\n")
    return tmp_path


def _close_sign_in_gap(repo):
    (repo / "src" / "pages" / "api" / "signin.ts").write_text(
        "export default handler\n// POST /api/auth/signin\n"
        "router.post('/api/auth/signin', signin)\n"
    )


# ── compare() matrix ────────────────────────────────────────────────────────


def test_compare_improvement():
    baseline = _report(70.0, "C", findings=[_finding("AWS key")], gaps=[_GAP])
    current = _report(82.5, "B")
    cmp = compare(current, baseline)
    assert cmp["score_delta"] == 12.5
    assert cmp["grade_change"] == "C→B"
    assert cmp["new_findings"] == [] and cmp["new_gaps"] == []
    assert [f["title"] for f in cmp["resolved_findings"]] == ["AWS key"]
    assert [g["flow"] for g in cmp["resolved_gaps"]] == ["sign_in"]
    assert cmp["regressed"] is False


def test_compare_new_high_finding_regresses_even_with_flat_score():
    baseline = _report(70.0, "C")
    current = _report(70.0, "C", findings=[_finding("leaked token")])
    cmp = compare(current, baseline)
    assert cmp["score_delta"] == 0.0
    assert cmp["grade_change"] is None
    assert [f["title"] for f in cmp["new_findings"]] == ["leaked token"]
    assert cmp["regressed"] is True


def test_compare_new_low_finding_within_tolerance_is_not_a_regression():
    baseline = _report(70.0, "C")
    current = _report(69.5, "C", findings=[_finding("lint nit", severity="low")])
    assert compare(current, baseline)["regressed"] is False
    # …but past the 0.5-point tolerance the score drop alone regresses.
    assert compare(_report(69.4, "C"), baseline)["regressed"] is True


def test_compare_new_gap_regresses():
    cmp = compare(_report(70.0, "C", gaps=[_GAP]), _report(70.0, "C"))
    assert [g["flow"] for g in cmp["new_gaps"]] == ["sign_in"]
    assert cmp["regressed"] is True


def test_compare_identity_is_gate_title_location():
    # Same (gate, title, location) on both sides: nothing new, nothing resolved.
    stable = _finding("AWS key")
    moved = _finding("AWS key", location="src/other.py:9")
    cmp = compare(_report(70.0, "C", findings=[stable, moved]),
                  _report(70.0, "C", findings=[stable]))
    assert [f["location"] for f in cmp["new_findings"]] == ["src/other.py:9"]
    assert cmp["resolved_findings"] == []


# ── history ─────────────────────────────────────────────────────────────────


def test_history_appends_and_trend(tmp_path):
    entries = append_history(tmp_path, _report(44.8, "F"))
    assert render_trend(entries) is None  # one run: nothing to compare
    entries = append_history(tmp_path, _report(49.0, "F", gaps=[_GAP]))
    entries = append_history(tmp_path, _report(75.5, "C"))

    lines = (tmp_path / "history.jsonl").read_text().splitlines()
    assert len(lines) == 3  # append-only, one line per run
    second = json.loads(lines[1])
    assert second == {"generated_at": "2026-01-01T00:00:00+00:00",
                      "score": 49.0, "grade": "F",
                      "findings_count": 0, "gaps_count": 1}
    assert render_trend(entries) == "history: 44.8 → 49.0 → 75.5 (3 runs)"


# ── HTML report: baseline + history sections ────────────────────────────────


def test_render_html_baseline_regression_section():
    from genesis_cli.report import render_html

    baseline = _report(70.0, "C")
    current = _report(62.0, "D", findings=[_finding("leaked token")], gaps=[_GAP])
    current["baseline_comparison"] = compare(current, baseline)
    out = render_html(current)
    assert "Changes since baseline" in out
    assert "REGRESSION" in out
    assert "score ▼ -8.0" in out and "grade C→D" in out
    # New findings/gaps carry the red class; the finding title is present.
    assert "base-new" in out and "leaked token" in out
    assert "+ gap: Implement returning-user sign-in (login)" in out


def test_render_html_baseline_improvement_shows_resolved_green():
    from genesis_cli.report import render_html

    baseline = _report(70.0, "C", findings=[_finding("AWS key")], gaps=[_GAP])
    current = _report(82.5, "B")
    current["baseline_comparison"] = compare(current, baseline)
    out = render_html(current)
    assert "Changes since baseline" in out
    assert "REGRESSION" not in out
    assert "base-resolved" in out and "− resolved: AWS key" in out
    assert "− closed gap: Implement returning-user sign-in (login)" in out


def test_render_html_history_trend_strip():
    from genesis_cli.report import render_html

    def _entry(score, grade):
        return {"generated_at": "2026-01-01T00:00:00+00:00", "score": score,
                "grade": grade, "findings_count": 0, "gaps_count": 0}

    report = _report(75.5, "C")
    history = [_entry(44.8, "F"), _entry(49.0, "F"), _entry(75.5, "C")]
    out = render_html(report, history=history)
    assert "Score history" in out and "3 runs" in out
    assert out.count("trend-bar") >= 3
    assert "75.5/100 (C)" in out  # tooltip on the current bar
    # Fewer than two runs (or no history): no strip, and no crash.
    assert "Score history" not in render_html(report, history=history[:1])
    assert "Score history" not in render_html(report)


# ── CLI: --baseline / --fail-on-regression ──────────────────────────────────


def test_cli_baseline_improvement_then_regression(tmp_path, capsys):
    repo = _seed_repo(tmp_path / "repo")
    out = tmp_path / "out"

    # Run 1: establish the baseline (repo has the sign_in gap).
    main(["adopt", str(repo), "--out", str(out)])
    capsys.readouterr()

    # Run 2: gap closed → improvement; --fail-on-regression must exit 0.
    _close_sign_in_gap(repo)
    code = main(["adopt", str(repo), "--out", str(out),
                 "--baseline", str(out / "report.json"),
                 "--fail-on-regression"])
    captured = capsys.readouterr()
    assert code == 0
    assert "CHANGES SINCE BASELINE" in captured.out
    assert "closed gap" in captured.out
    assert "history:" in captured.out and "(2 runs)" in captured.out

    report = json.loads((out / "report.json").read_text())
    cmp = report["baseline_comparison"]
    assert cmp["regressed"] is False
    assert [g["flow"] for g in cmp["resolved_gaps"]] == ["sign_in"]
    # The HTML artifact carries the baseline section and the history strip.
    html_out = (out / "report.html").read_text()
    assert "Changes since baseline" in html_out
    assert "Score history" in html_out

    # Run 3: gap reintroduced → regression against run 2's report; exit 1.
    (repo / "src" / "pages" / "api" / "signin.ts").unlink()
    code = main(["adopt", str(repo), "--out", str(out),
                 "--baseline", str(out / "report.json"),
                 "--fail-on-regression"])
    captured = capsys.readouterr()
    assert code == 1
    assert "REGRESSION" in captured.out
    assert "FAIL: regression since baseline" in captured.err
    cmp = json.loads((out / "report.json").read_text())["baseline_comparison"]
    assert cmp["regressed"] is True
    assert [g["flow"] for g in cmp["new_gaps"]] == ["sign_in"]
    # history kept growing across all three runs.
    assert len((out / "history.jsonl").read_text().splitlines()) == 3


def test_cli_fail_on_regression_requires_baseline(tmp_path, capsys):
    repo = _seed_repo(tmp_path)
    assert main(["adopt", str(repo), "--fail-on-regression"]) == 2
    assert "--fail-on-regression requires --baseline" in capsys.readouterr().err


def test_cli_unreadable_baseline_is_a_usage_error(tmp_path, capsys):
    repo = _seed_repo(tmp_path)
    code = main(["adopt", str(repo), "--out", str(tmp_path / "o"),
                 "--baseline", str(tmp_path / "missing.json")])
    assert code == 2
    assert "cannot read baseline" in capsys.readouterr().err
