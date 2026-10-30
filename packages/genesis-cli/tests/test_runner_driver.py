"""ClaudeDriver: stream-json parsing against a fake `claude` on PATH —
final-result extraction, assistant-text callback, oversized lines, env
hygiene (ANTHROPIC_API_KEY never reaches the child)."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import textwrap
from pathlib import Path

import pytest
from genesis_cli.runner.claude_driver import ClaudeDriver, check_cli

BIG = 300_000  # > asyncio's default 64 KB line cap


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch):
    def install(body: str) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        script = bin_dir / "claude"
        script.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    return install


STREAMING_BODY = f"""
import json, os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("1.2.3 (fake)")
    sys.exit(0)
# Record what the driver passed and what env the child saw.
open("argv.json", "w").write(json.dumps(args))
open("env.json", "w").write(json.dumps(
    {{"has_api_key": "ANTHROPIC_API_KEY" in os.environ}}))
print(json.dumps({{"type": "system", "session_id": "sess-fake"}}))
print(json.dumps({{"type": "assistant", "session_id": "sess-fake",
                   "message": {{"content": [{{"type": "text", "text": "hello "}},
                                            {{"type": "text", "text": "world"}}]}}}}))
# An oversized single line must not kill the stream.
print(json.dumps({{"type": "tool_result", "data": "x" * {BIG}}}))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
                   "session_id": "sess-fake", "total_cost_usd": None,
                   "usage": {{"input_tokens": 123, "output_tokens": 45}}}}))
"""


async def test_driver_parses_stream_and_scrubs_env(tmp_path, fake_claude, monkeypatch):
    fake_claude(STREAMING_BODY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    ok, detail = check_cli()
    assert ok and "1.2.3" in detail

    ws = tmp_path / "ws"
    ws.mkdir()
    texts: list[str] = []
    result = await ClaudeDriver().run(
        "build it", workdir=ws, resume_session="old-sess",
        on_text=texts.append)

    assert result["status"] == "completed"
    assert result["session_id"] == "sess-fake"
    assert result["usage"]["input_tokens"] == 123
    assert result["usage"]["output_tokens"] == 45
    assert result["usage"]["cost_usd"] is None  # subscription: no dollar cost
    assert result["usage"]["duration_s"] is not None
    assert result["error"] is None
    assert texts == ["hello world"]

    argv = json.loads((ws / "argv.json").read_text())
    assert argv[:2] == ["-p", "build it"]
    for flag in ("--output-format", "--verbose", "--add-dir",
                 "--permission-mode", "--resume"):
        assert flag in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert argv[argv.index("--resume") + 1] == "old-sess"
    assert argv[argv.index("--add-dir") + 1] == str(ws)
    # The subscription rule: the child never sees an API key.
    assert json.loads((ws / "env.json").read_text()) == {"has_api_key": False}


FAILING_BODY = """
import json, sys
if sys.argv[1:] == ["--version"]:
    print("1.2.3 (fake)"); sys.exit(0)
print(json.dumps({"type": "result", "subtype": "error", "is_error": True,
                  "session_id": "s2", "result": "budget exhausted",
                  "usage": {"input_tokens": 1, "output_tokens": 0}}))
sys.exit(1)
"""


async def test_driver_reports_cli_error(tmp_path, fake_claude):
    fake_claude(FAILING_BODY)
    ws = tmp_path / "ws"
    ws.mkdir()
    result = await ClaudeDriver().run("x", workdir=ws)
    assert result["status"] == "failed"
    assert "budget exhausted" in (result["error"] or "")


HANGING_BODY = """
import sys, time
if sys.argv[1:] == ["--version"]:
    print("1.2.3 (fake)"); sys.exit(0)
sys.stdout.write('{"type": "system", "session_id": "s3"}\\n')
sys.stdout.flush()
time.sleep(60)
"""


async def test_kill_marks_interrupted(tmp_path, fake_claude):
    fake_claude(HANGING_BODY)
    ws = tmp_path / "ws"
    ws.mkdir()
    driver = ClaudeDriver()
    task = asyncio.create_task(driver.run("x", workdir=ws))
    await asyncio.sleep(0.5)
    driver.kill()
    result = await asyncio.wait_for(task, timeout=10)
    assert result["status"] == "interrupted"


async def test_missing_cli_guidance(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    ok, detail = check_cli()
    assert ok is False and "PATH" in detail
