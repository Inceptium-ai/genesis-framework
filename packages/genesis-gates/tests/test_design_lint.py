# ruff: noqa: E501
"""design-lint gate + slop-regex provider.

One fixture file per G-rule proves each regex fires exactly once; a clean
fixture proves silence; config knobs (disable, rules_file) and the module-level
``lint_html`` helper are covered so the server can share the rule table.
"""
from __future__ import annotations

import textwrap

from genesis_gates.engine.base import GateContext, GateStatus, Severity
from genesis_gates.gates.design_lint import DesignLintGate
from genesis_gates.providers.slop_regex import (
    BUILTIN_RULES,
    SlopRegexProvider,
    lint_html,
    load_extra_rules,
)

# One representative offending line per mechanized rule.
OFFENDERS = {
    "G1": '<h1 className="bg-clip-text text-transparent">Hi</h1>',
    "G2": '<div className="bg-gradient-to-r from-blue-500 to-pink-500">x</div>',
    "G3": '<div className="backdrop-blur-md rounded-xl">card</div>',
    "G4": '<button className="glow-effect">Go</button>',
    "G5": '<div className="absolute blur-3xl bg-purple-500/30" />',
    "G6": '<NebulaCanvas density={0.4} />',
    "G7": '<span>🚀 Launch</span>',
    "G9": '<span className="uppercase text-xs tracking-wider block">Eyebrow</span>',
    "G10": '<div className="bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900" />',
}

CLEAN = textwrap.dedent("""\
    <div className="bg-surface border border-border rounded-lg p-4">
      <h2 className="text-lg font-semibold text-primary">Dashboard</h2>
      <p className="text-secondary">All quiet.</p>
    </div>
""")


def _ctx(repo, config=None) -> GateContext:
    return GateContext(repo_path=str(repo), manifest={}, config=config or {})


def test_every_builtin_rule_fires_exactly_once(tmp_path):
    for rid, line in OFFENDERS.items():
        (tmp_path / f"{rid.lower()}.jsx").write_text(line + "\n")
    result = DesignLintGate().run(_ctx(tmp_path))

    assert result.status == GateStatus.failed
    fired = sorted(f.raw["rule"] for f in result.findings)
    assert fired == sorted(OFFENDERS)  # each rule exactly once, none missing
    # Findings carry a location and a remediation naming the fix.
    for f in result.findings:
        assert ":" in (f.location or "")
        assert f.remediation


def test_clean_ui_passes(tmp_path):
    (tmp_path / "App.tsx").write_text(CLEAN)
    result = DesignLintGate().run(_ctx(tmp_path))
    assert result.status == GateStatus.passed
    assert "clean" in result.summary


def test_no_ui_files_skips(tmp_path):
    (tmp_path / "main.py").write_text("print('backend only')\n")
    result = DesignLintGate().run(_ctx(tmp_path))
    assert result.status == GateStatus.skipped


def test_skip_dirs_are_not_linted(tmp_path):
    for d in ("node_modules/pkg", "gss/examples", ".genesis-work/wireframes", "dist"):
        p = tmp_path / d
        p.mkdir(parents=True)
        (p / "bad.jsx").write_text(OFFENDERS["G1"])
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ok.tsx").write_text(CLEAN)
    result = DesignLintGate().run(_ctx(tmp_path))
    assert result.status == GateStatus.passed


def test_disable_skips_rule(tmp_path):
    (tmp_path / "a.jsx").write_text(OFFENDERS["G3"] + "\n")
    config = {"gates": {"design-lint": {"disable": ["G3"]}}}
    result = DesignLintGate().run(_ctx(tmp_path, config))
    assert result.status == GateStatus.passed


def test_rules_file_extends_the_ban_list(tmp_path):
    # A locked family bans its own pattern (e.g. rounded corners in Swiss).
    (tmp_path / ".genesis").mkdir()
    (tmp_path / ".genesis" / "design-rules.yaml").write_text(textwrap.dedent("""\
        rules:
          - id: F1
            name: rounded corners banned by family
            pattern: "rounded-(?:xl|2xl|3xl|full)"
            severity: low
            remediation: This family uses sharp corners only.
          - id: BROKEN
            pattern: "(unclosed"
    """))
    (tmp_path / "a.jsx").write_text('<div className="rounded-2xl">x</div>\n')
    config = {"gates": {"design-lint": {"rules_file": ".genesis/design-rules.yaml"}}}
    result = DesignLintGate().run(_ctx(tmp_path, config))

    assert result.status == GateStatus.failed
    assert [f.raw["rule"] for f in result.findings] == ["F1"]
    assert result.findings[0].severity == Severity.low  # custom severity honored
    # The malformed rule was skipped, not fatal — proven by the scan completing.


def test_fail_on_severity_policy(tmp_path):
    (tmp_path / "a.jsx").write_text(OFFENDERS["G5"] + "\n")
    # Threshold above the findings' severity -> findings surface but gate passes.
    config = {"gates": {"design-lint": {"fail_on_severity": "high"}}}
    result = DesignLintGate().run(_ctx(tmp_path, config))
    assert result.status == GateStatus.passed
    assert result.findings  # still reported for triage


def test_lint_html_shares_the_rule_table():
    findings = lint_html('<div className="backdrop-blur-lg">x</div>')
    assert [f.raw["rule"] for f in findings] == ["G3"]
    assert lint_html(CLEAN) == []
    # disable works for the in-process path too (bake-off critique).
    assert lint_html('<div className="backdrop-blur-lg">x</div>', disable={"G3"}) == []


def test_load_extra_rules_tolerates_garbage(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("rules: [not-a-dict, {id: X}, {pattern: no-id}]")
    assert load_extra_rules(f) == []
    f.write_text("{{{{not yaml")
    assert load_extra_rules(f) == []
    assert load_extra_rules(tmp_path / "missing.yaml") == []


def test_g8_is_deliberately_absent():
    # G8 (Inter-only typeface) is judgment-tier — a line regex can't see "the
    # only typeface". It lives in the design brief, not the linter.
    assert not any(r.id == "G8" for r in BUILTIN_RULES)


def test_provider_registered():
    from genesis_gates.engine.provider import PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY.get("slop-regex") is SlopRegexProvider
