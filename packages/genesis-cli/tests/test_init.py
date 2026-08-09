"""genesis init: scaffold an empty repo, idempotently, never overwriting."""

from __future__ import annotations

import yaml
from genesis_cli.init import run_init
from genesis_cli.main import main

EXPECTED = ("AGENTS.md", ".gitignore", ".env.example",
            ".github/workflows/genesis-audit.yml", ".genesis/config.yaml")


def test_empty_dir_scaffolded(tmp_path):
    repo = tmp_path / "fresh"
    created, skipped = run_init(repo)
    assert created == list(EXPECTED)
    assert skipped == []
    for rel in EXPECTED:
        assert (repo / rel).is_file(), rel
    # Empty repo -> the "establish a test command" AGENTS.md variant.
    agents = (repo / "AGENTS.md").read_text()
    assert "establish a test command" in agents.lower()
    gitignore = (repo / ".gitignore").read_text()
    assert ".env" in gitignore and ".genesis-report/" in gitignore
    assert "!.env.example" in gitignore
    # Generated workflow is valid YAML and marks the PyPI TODO clearly.
    wf = (repo / ".github/workflows/genesis-audit.yml").read_text()
    assert yaml.safe_load(wf)["name"] == "genesis-audit"
    assert "TODO" in wf and "not on PyPI" in wf
    # Gate config stub parses and passes the loader's schema validation.
    from genesis_gates.config import load_gate_config
    assert load_gate_config(repo) == {"apiVersion": "genesis.dev/v0",
                                      "kind": "GateConfig"}


def test_second_run_skips_everything(tmp_path):
    repo = tmp_path / "fresh"
    run_init(repo)
    created, skipped = run_init(repo)
    assert created == []
    assert skipped == list(EXPECTED)


def test_existing_files_never_overwritten(tmp_path):
    repo = tmp_path / "existing"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# hand-written — do not clobber\n")
    (repo / ".gitignore").write_text("custom\n")
    created, skipped = run_init(repo)
    assert "AGENTS.md" in skipped and ".gitignore" in skipped
    assert (repo / "AGENTS.md").read_text() == "# hand-written — do not clobber\n"
    assert (repo / ".gitignore").read_text() == "custom\n"
    assert ".env.example" in created  # the rest is still scaffolded


def test_cli_init_prints_summary(tmp_path, capsys):
    repo = tmp_path / "cli-repo"
    assert main(["init", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "created" in out and "AGENTS.md" in out and "Next steps" in out
    assert main(["init", str(repo)]) == 0
    assert "exists, skipped" in capsys.readouterr().out


def test_cli_init_rejects_non_directory(tmp_path):
    f = tmp_path / "a-file"
    f.write_text("x")
    assert main(["init", str(f)]) == 2
