"""Runner config: round-trip, restrictive mode, resilience to junk."""

from __future__ import annotations

import stat

import pytest
from genesis_cli.runner.config import (
    config_path,
    load_config,
    save_config,
    work_root,
)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("GENESIS_RUNNER_HOME", str(tmp_path / ".genesis"))


def test_round_trip_and_mode():
    path = save_config("https://genesis.example.com/", "grt_secret-token", "my laptop")
    assert path == config_path()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600

    cfg = load_config()
    assert cfg == {"server": "https://genesis.example.com/",
                   "token": "grt_secret-token", "name": "my laptop"}


def test_overwrite_keeps_mode_0600():
    save_config("https://a", "grt_1", "m")
    save_config("https://b", "grt_2", "m")
    assert stat.S_IMODE(config_path().stat().st_mode) == 0o600
    assert load_config()["token"] == "grt_2"


def test_values_with_quotes_and_backslashes_survive():
    save_config("https://a", 'grt_we"ird\\tok', 'na"me')
    cfg = load_config()
    assert cfg["token"] == 'grt_we"ird\\tok' and cfg["name"] == 'na"me'


def test_missing_or_bad_file_reads_empty():
    assert load_config() == {}
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text("not = valid = toml [")
    assert load_config() == {}


def test_work_root_is_created_under_runner_home(tmp_path):
    root = work_root()
    assert root.is_dir()
    assert root == tmp_path / ".genesis" / "runner-work"
