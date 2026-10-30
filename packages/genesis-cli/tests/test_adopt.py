"""genesis adopt: end-to-end audit on a synthetic repo (no external tools needed
— gates skip cleanly, the deterministic analyzers do the work)."""

from __future__ import annotations

import json

from genesis_cli.adopt import run_audit
from genesis_cli.agents_md import render_agents_md
from genesis_cli.main import main
from genesis_cli.report import render_html, render_terminal


def _seed_repo(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "demo", "scripts": {"test": "vitest", "build": "vite build"},
        "dependencies": {"next-auth": "^4.0.0"},
    }))
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  app:\n    environment:\n"
        "      API_SECRET_KEY: ${API_SECRET_KEY}\n"
        "      DB_HOST: ${DB_HOST:-postgres}\n"
    )
    src = tmp_path / "src" / "pages" / "api"
    src.mkdir(parents=True)
    (src / "signup.ts").write_text(
        "export default handler\n// POST /api/signup\n"
        "router.post('/api/auth/signup', signup)\n"
    )
    (tmp_path / "README.md").write_text("# demo\n")
    (tmp_path / ".gitignore").write_text("node_modules\n")
    return tmp_path


def test_run_audit_shapes_and_findings(tmp_path):
    repo = _seed_repo(tmp_path)
    report = run_audit(repo)

    assert report["kind"] == "GenesisReadinessReport"
    # auth marker (next-auth) detected; signup implemented but not sign-in.
    assert report["completeness"]["profiles"] == ["auth"]
    gap_flows = {g["flow"] for g in report["completeness"]["gaps"]}
    assert "sign_in" in gap_flows
    # compose var without default is flagged.
    missing = {d["env_var"] for d in report["env"]["missing_required"]}
    assert missing == {"API_SECRET_KEY"}
    # score is a number with categories that sum to it.
    r = report["readiness"]
    assert 0 <= r["score"] <= 100
    assert abs(sum(c["points"] for c in r["categories"]) - r["score"]) < 0.2


def test_documented_env_var_is_not_an_env_hygiene_gap(tmp_path):
    """env_hygiene must measure declaration, not what this machine holds.

    The seeded repo references ${API_SECRET_KEY} with no default; documenting
    it in `.env.example` is exactly the fix the report asks for, so the gap has
    to clear — previously it did not, which pinned the category at 0/10 in any
    checkout whose compose files reference undefaulted vars.
    """
    repo = _seed_repo(tmp_path)
    (repo / ".env.example").write_text("API_SECRET_KEY=\n")
    report = run_audit(repo)
    assert report["env"]["missing_required"] == []
    env_cat = next(c for c in report["readiness"]["categories"]
                   if c["key"] == "env_hygiene")
    assert env_cat["points"] > 0


def test_renderers_and_agents_md(tmp_path):
    report = run_audit(_seed_repo(tmp_path))
    term = render_terminal(report)
    assert "READINESS SCORE" in term
    html_out = render_html(report)
    assert "<!doctype html>" in html_out and "Readiness" in html_out
    # Completeness gaps are styled like high-severity findings.
    assert "class='gap'" in html_out and "required flow" in html_out
    # Footer links back to the project.
    assert "https://genesis.dev" in html_out
    agents = render_agents_md(report)
    assert "npm run test" in agents
    assert "sign-in" in agents.lower()  # gap surfaced as a priority task


def test_cli_writes_artifacts(tmp_path, capsys):
    repo = _seed_repo(tmp_path)
    out = tmp_path / "out"
    code = main(["adopt", str(repo), "--out", str(out)])
    assert code in (0, 1)  # grade-dependent, must not crash
    assert (out / "report.json").exists()
    assert (out / "report.html").exists()
    assert (out / "AGENTS.md").exists()
    sarif = json.loads((out / "report.sarif").read_text())
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["tool"]["driver"]["name"] == "genesis"
    # The seeded repo's sign_in gap must surface as a completeness result.
    assert any(r["ruleId"] == "completeness"
               for r in sarif["runs"][0]["results"])
    assert "READINESS SCORE" in capsys.readouterr().out


def test_cli_write_respects_existing_agents_md(tmp_path):
    repo = _seed_repo(tmp_path)
    (repo / "AGENTS.md").write_text("# hand-written — do not clobber\n")
    main(["adopt", str(repo), "--out", str(tmp_path / "o"), "--write"])
    assert "hand-written" in (repo / "AGENTS.md").read_text()  # no overwrite
    main(["adopt", str(repo), "--out", str(tmp_path / "o2"), "--write", "--force"])
    assert "hand-written" not in (repo / "AGENTS.md").read_text()
