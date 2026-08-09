"""Direct tests for the generic BYO / defer-to-CI providers.

We construct each ``Provider(settings)`` and a ``GateContext`` directly and call
``.run(ctx)`` — no gates involved (the gates are being refactored separately).
Providers leave ``result.gate`` blank; the gate stamps it, so we assert on
status/findings/summary only.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from genesis_gates.engine.base import GateContext, GateStatus, Severity
from genesis_gates.providers.command import CommandProvider
from genesis_gates.providers.external import ExternalProvider
from genesis_gates.providers.report import ReportProvider

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORTS = REPO_ROOT / "examples" / "reports"


def ctx(repo_path: str | Path = ".") -> GateContext:
    return GateContext(repo_path=str(repo_path), manifest={}, config={})


# --- report provider ------------------------------------------------------- #

def test_report_sarif_two_findings_failed(tmp_path: Path) -> None:
    prov = ReportProvider({"report": str(REPORTS / "sample.sarif"), "parser": "sarif"})
    res = prov.run(ctx())
    assert res.status is GateStatus.failed
    assert len(res.findings) == 2
    # worst finding is the error-level SQL injection -> high
    assert any(f.severity is Severity.high for f in res.findings)


def test_report_junit_failure_finding() -> None:
    prov = ReportProvider({"report": str(REPORTS / "junit.xml"), "parser": "junit"})
    res = prov.run(ctx())
    assert res.status is GateStatus.failed
    assert len(res.findings) == 1
    assert res.findings[0].severity is Severity.high


def test_report_cyclonedx_info_and_passed(tmp_path: Path) -> None:
    sbom = tmp_path / "sbom.json"
    sbom.write_text('{"bomFormat": "CycloneDX", "components": [{"name": "a"}, {"name": "b"}]}')
    prov = ReportProvider({"report": "sbom.json", "parser": "cyclonedx"})
    res = prov.run(ctx(tmp_path))
    assert res.status is GateStatus.passed  # SBOM never fails
    assert len(res.findings) == 1
    assert res.findings[0].severity is Severity.info
    assert res.findings[0].title == "SBOM: 2 components"


def test_report_missing_file_is_error() -> None:
    prov = ReportProvider({"report": "does/not/exist.sarif", "parser": "sarif"})
    res = prov.run(ctx())
    assert res.status is GateStatus.error
    assert "not found" in res.summary


def test_report_relative_path_resolves_against_repo_path() -> None:
    prov = ReportProvider({"report": "examples/reports/sample.sarif", "parser": "sarif"})
    res = prov.run(ctx(REPO_ROOT))
    assert res.status is GateStatus.failed


def test_report_unknown_parser_is_error() -> None:
    prov = ReportProvider({"report": str(REPORTS / "sample.sarif"), "parser": "bogus"})
    res = prov.run(ctx())
    assert res.status is GateStatus.error
    assert "unknown parser" in res.summary


def test_report_unsupported_native_parser_is_error() -> None:
    prov = ReportProvider({"report": str(REPORTS / "sample.sarif"), "parser": "trivy-json"})
    res = prov.run(ctx())
    assert res.status is GateStatus.error
    assert "not yet supported" in res.summary


# --- command provider ------------------------------------------------------ #

def test_command_passes_on_success() -> None:
    prov = CommandProvider({"command": [sys.executable, "-c", "print()"]})
    res = prov.run(ctx())
    assert res.status is GateStatus.passed
    assert res.findings == []


def test_command_string_form_is_split() -> None:
    prov = CommandProvider({"command": f"{sys.executable} -c print()"})
    # "print()" with no import is a valid no-op statement.
    res = prov.run(ctx())
    assert res.status is GateStatus.passed


def test_command_fail_on_nonzero_failed() -> None:
    prov = CommandProvider(
        {"command": [sys.executable, "-c", "import sys; sys.exit(3)"], "fail_on_nonzero": True}
    )
    res = prov.run(ctx())
    assert res.status is GateStatus.failed
    assert "rc=3" in res.summary


def test_command_nonzero_without_flag_passes() -> None:
    prov = CommandProvider({"command": [sys.executable, "-c", "import sys; sys.exit(3)"]})
    res = prov.run(ctx())
    assert res.status is GateStatus.passed  # non-zero ignored without fail_on_nonzero


def test_command_missing_binary_skipped() -> None:
    prov = CommandProvider({"command": ["genesis-nonexistent-binary-xyz"]})
    res = prov.run(ctx())
    assert res.status is GateStatus.skipped
    assert "not found" in res.summary


def test_command_with_parser_findings_failed() -> None:
    # Emit a SARIF doc to stdout and parse it -> findings -> failed.
    sarif = (REPORTS / "sample.sarif").read_text()
    prog = "import sys; sys.stdout.write(open(sys.argv[1]).read())"
    prov = CommandProvider(
        {"command": [sys.executable, "-c", prog, str(REPORTS / "sample.sarif")], "parser": "sarif"}
    )
    res = prov.run(ctx())
    assert res.status is GateStatus.failed
    assert len(res.findings) == 2
    assert sarif  # fixture sanity


def test_command_unparseable_output_is_error() -> None:
    prov = CommandProvider(
        {"command": [sys.executable, "-c", "print('not json')"], "parser": "sarif"}
    )
    res = prov.run(ctx())
    assert res.status is GateStatus.error
    assert "could not parse" in res.summary


def test_command_unknown_parser_is_error() -> None:
    prov = CommandProvider({"command": [sys.executable, "-c", "print()"], "parser": "bogus"})
    res = prov.run(ctx())
    assert res.status is GateStatus.error


def test_command_missing_command_is_error() -> None:
    prov = CommandProvider({})
    res = prov.run(ctx())
    assert res.status is GateStatus.error


# --- external provider ----------------------------------------------------- #

def test_external_passed_with_attested_summary() -> None:
    prov = ExternalProvider({"attested_by": "Acme CI / gitleaks step"})
    res = prov.run(ctx())
    assert res.status is GateStatus.passed
    assert res.summary == "attested externally by Acme CI / gitleaks step"
    assert res.findings == []


def test_external_includes_note_in_summary() -> None:
    prov = ExternalProvider({"attested_by": "Acme CI", "note": "org-wide"})
    res = prov.run(ctx())
    assert "attested externally by Acme CI" in res.summary
    assert "org-wide" in res.summary


def test_external_respects_status_override() -> None:
    prov = ExternalProvider({"attested_by": "Acme CI", "status": "skipped"})
    res = prov.run(ctx())
    assert res.status is GateStatus.skipped


def test_external_missing_attested_by_is_error() -> None:
    prov = ExternalProvider({})
    res = prov.run(ctx())
    assert res.status is GateStatus.error


# --- trivy provider: repo-shipped ignore file ------------------------------ #

def _run_trivy_capturing(monkeypatch, tmp_path: Path) -> list[str]:
    """Run TrivyProvider against tmp_path with trivy stubbed; return its argv."""
    import genesis_gates.providers.trivy as trivy_mod
    from genesis_gates.util import CmdResult

    captured: list[str] = []

    def fake_run_cmd(cmd, **kwargs):
        captured.extend(cmd)
        return CmdResult(rc=0, stdout='{"Results": []}', stderr="")

    monkeypatch.setattr(trivy_mod, "which", lambda *a: "/usr/bin/trivy")
    monkeypatch.setattr(trivy_mod, "run_cmd", fake_run_cmd)
    res = trivy_mod.TrivyProvider({}).run(ctx(tmp_path))
    assert res.status is GateStatus.passed
    return captured


def test_trivy_passes_repo_ignorefile_when_present(
    monkeypatch, tmp_path: Path
) -> None:
    ignore = tmp_path / ".trivyignore.yaml"
    ignore.write_text("vulnerabilities: []\n")
    argv = _run_trivy_capturing(monkeypatch, tmp_path)
    assert "--ignorefile" in argv
    assert argv[argv.index("--ignorefile") + 1] == str(ignore)


def test_trivy_prefers_yaml_ignorefile_over_plain(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".trivyignore").write_text("CVE-0000-0000\n")
    yaml_ignore = tmp_path / ".trivyignore.yaml"
    yaml_ignore.write_text("vulnerabilities: []\n")
    argv = _run_trivy_capturing(monkeypatch, tmp_path)
    assert argv[argv.index("--ignorefile") + 1] == str(yaml_ignore)


def test_trivy_omits_ignorefile_when_absent(monkeypatch, tmp_path: Path) -> None:
    argv = _run_trivy_capturing(monkeypatch, tmp_path)
    assert "--ignorefile" not in argv


# --- registration ---------------------------------------------------------- #

def test_generic_providers_are_registered() -> None:
    # Importing the package triggers auto-registration of each module.
    import genesis_gates.providers  # noqa: F401
    from genesis_gates.engine.provider import PROVIDER_REGISTRY

    assert PROVIDER_REGISTRY.get("command") is CommandProvider
    assert PROVIDER_REGISTRY.get("report") is ReportProvider
    assert PROVIDER_REGISTRY.get("external") is ExternalProvider


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
