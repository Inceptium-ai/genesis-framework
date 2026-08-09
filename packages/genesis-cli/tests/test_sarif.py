"""SARIF 2.1.0 renderer: structure, rule derivation, level mapping, locations."""

from __future__ import annotations

from genesis_cli.sarif import render_sarif

CANNED_REPORT = {
    "kind": "GenesisReadinessReport",
    "repo": "/work/demo",
    "gates": [
        {
            "gate": "secret-scan", "status": "failed", "summary": "1 secret",
            "findings": [
                {"gate": "secret-scan", "title": "AWS key committed",
                 "severity": "critical", "detail": "AKIA... in source",
                 "location": "/work/demo/src/config.py:42"},
            ],
        },
        {
            "gate": "dependency-scan", "status": "failed", "summary": "2 vulns",
            "findings": [
                {"gate": "dependency-scan", "title": "CVE-2024-0001 in lodash",
                 "severity": "medium", "detail": "", "location": "package.json"},
                {"gate": "dependency-scan", "title": "CVE-2024-0002 in left-pad",
                 "severity": "low", "detail": "prototype pollution",
                 "location": None},
            ],
        },
        {"gate": "sast", "status": "passed", "summary": "clean", "findings": []},
    ],
    "completeness": {
        "profiles": ["auth"],
        "gaps": [{"profile": "auth", "flow": "sign_in",
                  "title": "Implement returning-user sign-in (login)",
                  "brief": "Signup exists but no sign-in."}],
    },
    "env": {
        "missing_required": [
            {"env_var": "API_SECRET_KEY", "type": "credential",
             "description": "Session signing key."},
        ],
    },
}


def test_sarif_envelope_and_rules():
    log = render_sarif(CANNED_REPORT)
    assert log["version"] == "2.1.0"
    assert "sarif-schema-2.1.0" in log["$schema"]
    driver = log["runs"][0]["tool"]["driver"]
    assert driver["name"] == "genesis"
    assert driver["version"]
    assert driver["informationUri"]
    rule_ids = [r["id"] for r in driver["rules"]]
    assert rule_ids  # non-empty, and covers every source of findings
    assert set(rule_ids) == {"secret-scan", "dependency-scan", "completeness",
                             "env-contract"}


def test_every_result_references_a_declared_rule():
    log = render_sarif(CANNED_REPORT)
    run = log["runs"][0]
    declared = {r["id"] for r in run["tool"]["driver"]["rules"]}
    assert run["results"]  # one per finding + gap + missing env var
    assert len(run["results"]) == 5
    for result in run["results"]:
        assert result["ruleId"] in declared
        assert result["message"]["text"]


def test_severity_to_level_mapping():
    results = render_sarif(CANNED_REPORT)["runs"][0]["results"]
    by_rule: dict[str, list] = {}
    for r in results:
        by_rule.setdefault(r["ruleId"], []).append(r)
    assert by_rule["secret-scan"][0]["level"] == "error"        # critical
    assert by_rule["dependency-scan"][0]["level"] == "warning"  # medium
    assert by_rule["dependency-scan"][1]["level"] == "note"     # low
    assert by_rule["completeness"][0]["level"] == "error"
    assert by_rule["env-contract"][0]["level"] == "warning"


def test_locations_are_repo_relative_with_parsed_line():
    results = render_sarif(CANNED_REPORT)["runs"][0]["results"]
    secret = next(r for r in results if r["ruleId"] == "secret-scan")
    loc = secret["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "src/config.py"  # repo-relative
    assert loc["region"]["startLine"] == 42
    # Location without a :line suffix -> no region; no location -> no key.
    dep_with, dep_without = (
        r for r in results if r["ruleId"] == "dependency-scan")
    dloc = dep_with["locations"][0]["physicalLocation"]
    assert dloc["artifactLocation"]["uri"] == "package.json"
    assert "region" not in dloc
    assert "locations" not in dep_without


def test_unusable_locations_are_omitted():
    from genesis_cli.sarif import _location

    repo = "/work/demo"
    assert _location(None, repo) is None
    assert _location("/work/demo", repo) is None       # the repo root itself
    assert _location("/elsewhere/x.py:3", repo) is None  # outside the repo
    loc = _location("./src/a.py:7", repo)
    assert loc[0]["physicalLocation"]["artifactLocation"]["uri"] == "src/a.py"
    assert loc[0]["physicalLocation"]["region"]["startLine"] == 7


def test_messages_join_title_and_detail_and_brief():
    results = render_sarif(CANNED_REPORT)["runs"][0]["results"]
    texts = [r["message"]["text"] for r in results]
    assert "AWS key committed: AKIA... in source" in texts
    gap = next(r for r in results if r["ruleId"] == "completeness")
    assert "sign-in" in gap["message"]["text"]
    assert "Signup exists but no sign-in." in gap["message"]["text"]
    env = next(r for r in results if r["ruleId"] == "env-contract")
    assert "API_SECRET_KEY" in env["message"]["text"]
