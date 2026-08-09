"""`genesis gates`: per-gate table, --gate validation, --json package shape.

Runs on a synthetic repo with no scanner requirements — gates either pass with
built-in providers or skip cleanly; either way the command must stay graceful.
"""

from __future__ import annotations

import json

from genesis_cli.adopt import ADOPT_GATES
from genesis_cli.gates import gates_exit_code, render_gates_terminal
from genesis_cli.main import main

_STATUSES = {"passed", "failed", "skipped", "error"}


def _seed_repo(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "demo", "scripts": {"test": "vitest"},
        "dependencies": {"next-auth": "^4.0.0"},
    }))
    (tmp_path / "README.md").write_text("# demo\n")
    (tmp_path / ".gitignore").write_text("node_modules\n")
    return tmp_path


def test_gates_table_on_seeded_repo(tmp_path, capsys):
    code = main(["gates", str(_seed_repo(tmp_path))])
    out = capsys.readouterr().out
    assert code in (0, 1)  # environment-dependent (scanners may be installed)
    assert "GATE" in out and "STATUS" in out
    for name in ADOPT_GATES:  # every adopt gate appears exactly once
        assert out.count(name) == 1


def test_gates_unknown_gate_lists_valid_names(tmp_path, capsys):
    code = main(["gates", str(_seed_repo(tmp_path)), "--gate", "sekret-scan"])
    err = capsys.readouterr().err
    assert code == 2
    assert "unknown gate(s): sekret-scan" in err
    assert "secret-scan" in err and "unit-tests" in err  # valid names listed


def test_gates_json_shape_and_filter(tmp_path, capsys):
    code = main(["gates", str(_seed_repo(tmp_path)),
                 "--gate", "secret-scan", "--json"])
    pkg = json.loads(capsys.readouterr().out)
    assert pkg["kind"] == "EvidencePackage"
    results = pkg["gate_results"]
    assert [r["gate"] for r in results] == ["secret-scan"]  # filter honored
    assert all(r["status"] in _STATUSES for r in results)  # graceful, no crash
    assert "findings" in results[0]
    expected = 0 if all(r["status"] in ("passed", "skipped")
                        for r in results) else 1
    assert code == expected


def test_gates_missing_path_is_usage_error(tmp_path, capsys):
    assert main(["gates", str(tmp_path / "nope")]) == 2
    assert "not a directory" in capsys.readouterr().err


def test_render_failed_gate_shows_top_findings():
    pkg = {"gate_results": [
        {"gate": "secret-scan", "status": "failed", "summary": "7 secrets",
         "findings": [{"gate": "secret-scan", "severity": "high",
                       "title": f"secret {i}", "detail": "",
                       "location": f"src/app.py:{i}", "remediation": "rotate it"}
                      for i in range(7)]},
        {"gate": "sbom", "status": "skipped", "summary": "syft not installed",
         "findings": []},
    ]}
    text = render_gates_terminal(pkg)
    assert "secret-scan" in text and "7 secrets" in text
    assert "syft not installed" in text
    # Top 5 findings with location + remediation; the rest summarized.
    assert "secret 0" in text and "secret 4" in text and "secret 5" not in text
    assert "at src/app.py:0" in text and "fix: rotate it" in text
    assert "… 2 more" in text
    assert gates_exit_code(pkg) == 1


def test_exit_code_passed_and_skipped_is_green():
    pkg = {"gate_results": [{"gate": "a", "status": "passed", "findings": []},
                            {"gate": "b", "status": "skipped", "findings": []}]}
    assert gates_exit_code(pkg) == 0
    pkg["gate_results"][0]["status"] = "error"
    assert gates_exit_code(pkg) == 1
