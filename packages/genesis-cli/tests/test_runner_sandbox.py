"""The runner's OS sandbox (0.3.2). These tests cover how the sandbox is
BUILT and that it fails closed; containment itself is exercised against the
real OS in ``test_real_seatbelt_containment`` (macOS only)."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest
from genesis_cli.runner.sandbox import Sandbox, SandboxUnavailable, available, describe


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    for d in (".claude", "Library/Keychains", ".ssh", "Documents", ".npm",
              ".local/bin", ".nvm/versions/node/v20/bin", "proj/.venv/bin",
              "checkout/bin", "checkout/.git"):
        (home / d).mkdir(parents=True)
    (home / ".claude.json").write_text("{}")
    (home / "proj/.venv/pyvenv.cfg").write_text("home = /usr/bin\n")
    return home


def _box(tmp_path, system="Darwin", path_env=""):
    home = _home(tmp_path)
    work = home / ".genesis/runner-work/p/task-V1_T1"
    work.mkdir(parents=True)
    return Sandbox(workdir=work, tmpdir=work.parent / ".tmp-task-V1_T1",
                   home=home, system=system, path_env=path_env), home


def test_only_the_short_list_is_reachable_never_the_rest_of_home(tmp_path):
    box, home = _box(tmp_path)
    writable = {str(p.relative_to(home)) for p in box.writable_paths()}
    assert ".genesis/runner-work/p/task-V1_T1" in writable
    assert {".claude", ".claude.json", "Library/Keychains", ".npm"} <= writable
    readable = writable | {str(p.relative_to(home)) for p in box.readonly_home_paths()}
    assert ".local/bin" in readable
    for secret in (".ssh", "Documents"):
        assert secret not in readable
    # Nothing that does not exist is granted.
    assert all(p.exists() or p == box.tmpdir for p in box.writable_paths())


def test_toolchains_under_home_are_found_from_path(tmp_path):
    home = _home(tmp_path)
    path_env = os.pathsep.join([
        "/usr/bin",
        str(home / ".nvm/versions/node/v20/bin"),   # install root comes too
        str(home / "proj/.venv/bin"),               # a virtualenv
        str(home / "checkout/bin"),                 # a git checkout: bin only
        str(home / ".local/bin"),                   # never all of ~/.local
        str(home),                                  # never the home itself
    ])
    box, _ = _box(tmp_path.joinpath("x"), path_env="")  # separate tree, unused
    box = Sandbox(workdir=home / "w", tmpdir=home / "t", home=home,
                  system="Darwin", path_env=path_env)
    got = {str(p.relative_to(home.resolve())) for p in box._toolchain_paths()}
    assert got == {".nvm/versions/node/v20/bin", ".nvm/versions/node/v20",
                   "proj/.venv/bin", "proj/.venv", "checkout/bin", ".local/bin"}


def test_seatbelt_profile_denies_then_grants(tmp_path):
    box, home = _box(tmp_path)
    profile = box.seatbelt_profile()
    real_home = os.path.realpath(home)
    assert '(deny file-write* (subpath "/"))' in profile
    assert f'(deny file-read* (subpath "{real_home}"))' in profile
    assert f'(subpath "{real_home}/.claude")' in profile
    assert f'"{real_home}/.ssh"' not in profile
    # Network: web ports out, nothing on this machine.
    assert '(deny network*)' in profile
    assert '(remote tcp "*:443")' in profile
    assert profile.rstrip().endswith('(deny network-outbound (remote ip "localhost:*"))')
    # The grant lists come AFTER the denies (Seatbelt: last match wins).
    assert profile.index("(deny file-read*") < profile.index("(allow file-read*\n")


def test_no_credential_is_ever_passed_in(tmp_path):
    """A personal Claude subscription credential must not be handled by a
    third-party tool: the sandbox adds only temp-dir variables and never a
    token; the CLI's own login is left where the CLI keeps it."""
    box, _ = _box(tmp_path)
    env = box.env({"PATH": "/usr/bin", "HOME": "/h"})
    assert set(env) == {"PATH", "HOME", "TMPDIR", "CLAUDE_CODE_TMPDIR"}
    assert env["CLAUDE_CODE_TMPDIR"] == str(box.tmpdir)
    source = Path(__import__("genesis_cli.runner.sandbox", fromlist=["x"]).__file__).read_text()
    code = "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith(("#", '"', "-", "Credentials", "official",
                                                     "macOS Keychain", "reachable", "design")))
    assert "OAUTH_TOKEN" not in code and "ANTHROPIC_API_KEY" not in code


def test_bwrap_hides_home_and_mounts_back_the_short_list(tmp_path, monkeypatch):
    box, home = _box(tmp_path, system="Linux")
    args = box.bwrap_args()
    assert args[args.index("--tmpfs") + 1] == "/tmp"
    assert ["--tmpfs", str(home)] == args[args.index(str(home)) - 1:args.index(str(home)) + 1]
    binds = [args[i + 1] for i, a in enumerate(args) if a == "--bind"]
    assert str(box.workdir) in binds and str(home / ".claude") in binds
    assert str(home / ".ssh") not in args
    assert "--unshare-pid" in args and "--die-with-parent" in args


def test_fails_closed_when_no_sandbox_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    for system in ("Darwin", "Linux", "Windows"):
        box, _ = _box(tmp_path / system, system=system)
        with pytest.raises(SandboxUnavailable):
            box.wrap(["claude", "-p", "x"])
        assert available(system) is False
        assert "UNAVAILABLE" in describe(system)


async def test_driver_never_runs_a_sandboxed_task_bare(tmp_path, monkeypatch):
    from genesis_cli.runner import sandbox as sandbox_mod
    from genesis_cli.runner.claude_driver import ClaudeDriver

    launched = []

    async def _spawn(*a, **kw):
        launched.append(a)
        raise AssertionError("claude must not be launched")

    monkeypatch.setattr("asyncio.create_subprocess_exec", _spawn)
    monkeypatch.setattr(sandbox_mod.shutil, "which", lambda name: None)
    out = await ClaudeDriver(sandboxed=True, sandbox_root=tmp_path).run(
        "p", workdir=tmp_path)
    assert out["status"] == "failed" and "sandbox unavailable" in out["error"]
    assert launched == []


def test_runner_refuses_to_start_without_a_sandbox(monkeypatch, capsys):
    from genesis_cli.runner import loop as loop_mod
    from genesis_cli.runner import sandbox as sandbox_mod

    monkeypatch.setattr(loop_mod, "load_config", lambda: {"server": "http://s", "token": "t"})
    monkeypatch.setattr(loop_mod, "check_cli", lambda: (True, "2.0"))
    monkeypatch.setattr(sandbox_mod, "available", lambda system=None: False)
    monkeypatch.setattr(loop_mod.asyncio, "run",
                        lambda coro: (_ for _ in ()).throw(AssertionError("must not start")))
    assert loop_mod.start() == 2
    assert "--no-sandbox" in capsys.readouterr().err


@pytest.mark.skipif(platform.system() != "Darwin" or not shutil.which("sandbox-exec"),
                    reason="needs macOS Seatbelt")
def test_real_seatbelt_containment(tmp_path):
    """Against the real OS: a contained process writes its task dir and
    nothing else, and cannot read a file elsewhere under the home dir."""
    home = Path.home()
    work = home / ".genesis" / "sandbox-selftest" / f"task-{os.getpid()}"
    secret = home / f".genesis-sandbox-selftest-{os.getpid()}.txt"
    work.mkdir(parents=True, exist_ok=True)
    secret.write_text("SECRET")
    try:
        box = Sandbox(workdir=work, tmpdir=work.parent / f".tmp-{os.getpid()}")

        def run(script: str) -> subprocess.CompletedProcess:
            return subprocess.run(box.wrap(["/bin/sh", "-c", script]), cwd=str(work),
                                  env=box.env(dict(os.environ)),
                                  capture_output=True, text=True, timeout=30)

        assert run("echo ok > inside.txt && cat inside.txt").stdout.strip() == "ok"
        assert run('echo t > "$TMPDIR/t.txt"').returncode == 0
        assert run(f"cat {secret}").returncode != 0
        assert run(f"ls {home}/Documents").returncode != 0
        escape = home / f".genesis-sandbox-escape-{os.getpid()}.txt"
        assert run(f"echo x > {escape}").returncode != 0
        assert not escape.exists()
        assert run("echo x > /tmp/genesis-selftest-escape.txt").returncode != 0
    finally:
        secret.unlink(missing_ok=True)
        shutil.rmtree(work.parent, ignore_errors=True)
