"""Planning completions on the runner (0.3.0): one-shot `claude -p` driving
against a fake CLI on PATH, capability advertisement on claim, and the loop's
completion-item round trip. Offline only — nothing here talks to a network.
"""

from __future__ import annotations

import json
import os
import stat
import textwrap
from pathlib import Path

import httpx
import pytest
from genesis_cli.runner.claude_driver import ClaudeDriver, _final_json_object
from genesis_cli.runner.client import CAPABILITIES, RunnerClient


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch):
    """Install a stub `claude` on PATH. One-shot print mode records its argv
    and env to $T047_RECORD (cwd is a throwaway dir the driver deletes)."""

    def install(body: str) -> Path:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        script = bin_dir / "claude"
        script.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        record = tmp_path / "record.json"
        monkeypatch.setenv("T047_RECORD", str(record))
        return record

    return install


ONESHOT_BODY = """
import json, os, sys
args = sys.argv[1:]
open(os.environ["T047_RECORD"], "w").write(json.dumps(
    {"argv": args, "has_api_key": "ANTHROPIC_API_KEY" in os.environ,
     "cwd": os.getcwd()}))
# Wrapper noise before the result object must not break parsing.
print("Update available! run claude update")
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": '{"name": "zed"}', "session_id": "sess-1",
                  "total_cost_usd": 0,
                  "usage": {"input_tokens": 42, "output_tokens": 7}}))
"""


async def test_run_completion_oneshot_flags_and_parse(fake_claude, monkeypatch):
    record = fake_claude(ONESHOT_BODY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    out = await ClaudeDriver().run_completion(
        system="You are a planner.",
        messages=[{"role": "user", "content": "make zed"}])
    assert out["status"] == "completed"
    assert out["text"] == '{"name": "zed"}'
    assert out["usage"]["input_tokens"] == 42
    assert out["usage"]["output_tokens"] == 7
    assert out["usage"]["duration_s"] is not None

    rec = json.loads(record.read_text())
    argv = rec["argv"]
    # The verified one-shot invocation, exactly: print mode, single JSON
    # result, no tools, no persisted session, explicit system prompt.
    assert argv[:2] == ["-p", "make zed"]
    assert argv[2:4] == ["--output-format", "json"]
    assert argv[4:6] == ["--tools", ""]
    assert argv[6] == "--no-session-persistence"
    assert argv[7:9] == ["--system-prompt", "You are a planner."]
    # Subscription hygiene + isolation: no API key, throwaway empty cwd.
    assert rec["has_api_key"] is False
    assert "genesis-completion-" in rec["cwd"]
    assert not Path(rec["cwd"]).exists()  # cleaned up after the call


async def test_run_completion_flattens_multi_turn(fake_claude):
    record = fake_claude(ONESHOT_BODY)
    await ClaudeDriver().run_completion(
        system=None,
        messages=[{"role": "user", "content": "go"},
                  {"role": "assistant", "content": "{bad"},
                  {"role": "user", "content": "fix it"}])
    argv = json.loads(record.read_text())["argv"]
    prompt = argv[1]
    assert "[USER]\ngo" in prompt and "[ASSISTANT]\n{bad" in prompt
    assert "--system-prompt" not in argv


async def test_run_completion_error_result(fake_claude):
    fake_claude("""
import json
print(json.dumps({"type": "result", "subtype": "success", "is_error": True,
                  "result": "Not logged in", "usage": {}}))
""")
    out = await ClaudeDriver().run_completion(
        system=None, messages=[{"role": "user", "content": "go"}])
    assert out["status"] == "failed"
    assert "Not logged in" in out["error"]


async def test_run_completion_no_json_is_failed(fake_claude):
    fake_claude("""
import sys
print("something went sideways")
sys.exit(3)
""")
    out = await ClaudeDriver().run_completion(
        system=None, messages=[{"role": "user", "content": "go"}])
    assert out["status"] == "failed" and out["text"] is None


def test_final_json_object_strips_wrappers():
    noise = 'MOTD banner\n{"type": "result", "result": "ok"}\ntrailing text'
    assert _final_json_object(noise)["result"] == "ok"
    assert _final_json_object("no json here") is None
    # Embedded (non-line-aligned) object still found; the last one wins.
    assert _final_json_object('x {"a": 1} y {"b": 2} z') == {"b": 2}


async def test_claim_advertises_capabilities(monkeypatch):
    assert "completion" in CAPABILITIES
    client = RunnerClient("http://test", "grt_x")
    captured: dict = {}

    async def fake_post(url, **kw):
        captured["url"] = url
        captured["json"] = kw.get("json")
        return httpx.Response(204, request=httpx.Request("POST", f"http://test{url}"))

    monkeypatch.setattr(client._client, "post", fake_post)
    try:
        assert await client.claim() is None
    finally:
        await client.aclose()
    assert captured["url"] == "/api/runner/claim"
    assert captured["json"]["capabilities"] == ["completion"]
    # 0.3.1 also advertises its version when package metadata is present.
    assert set(captured["json"]) <= {"capabilities", "version"}


async def test_loop_runs_completion_item_and_posts_text(fake_claude):
    from genesis_cli.runner.loop import _run_completion_item, _Stop

    fake_claude(ONESHOT_BODY)

    class _FakeClient:
        def __init__(self):
            self.results: list[tuple[str, dict]] = []

        async def post_result(self, item_id, payload, tar_path=None):
            assert tar_path is None  # completions never upload an archive
            self.results.append((item_id, payload))

    client = _FakeClient()
    await _run_completion_item(client, {
        "item_id": "item-1", "kind": "completion",
        "task": {"system": "sys", "max_tokens": 128,
                 "messages": [{"role": "user", "content": "make zed"}]},
    }, _Stop())
    (item_id, payload), = client.results
    assert item_id == "item-1"
    assert payload["status"] == "completed"
    assert payload["text"] == '{"name": "zed"}'
    assert payload["deleted"] == [] and payload["session_id"] is None


async def test_completion_driver_model_from_task_with_opus_fallback(monkeypatch):
    """The server names the claude model alias per item (planning → opus);
    an older server omits it and the driver still pins 'opus' rather than
    inheriting the machine's personal claude default."""
    from genesis_cli.runner import loop as loop_mod

    seen: list[str | None] = []

    class _FakeDriver:
        def __init__(self, model=None):
            seen.append(model)
            self.model = model

        async def run_completion(self, **kw):
            return {"status": "completed", "text": "{}", "usage": {}}

    class _FakeClient:
        async def post_result(self, item_id, payload, tar_path=None):
            return None

    monkeypatch.setattr(loop_mod, "ClaudeDriver", _FakeDriver)
    stop = loop_mod._Stop()
    await loop_mod._run_completion_item(_FakeClient(), {
        "item_id": "i1", "task": {"messages": [{"role": "user", "content": "x"}],
                                  "model": "opus"}}, stop)
    await loop_mod._run_completion_item(_FakeClient(), {
        "item_id": "i2", "task": {"messages": [{"role": "user", "content": "x"}]}},
        stop)
    assert seen == ["opus", "opus"]


async def test_claim_sends_version_and_captures_latest_header(monkeypatch):
    from genesis_cli.runner import client as client_mod

    monkeypatch.setattr(client_mod, "own_version", lambda: "0.3.1")
    client = RunnerClient("http://test", "grt_x")
    captured: dict = {}

    async def fake_post(url, **kw):
        captured["json"] = kw.get("json")
        return httpx.Response(
            204, headers={"X-Genesis-Runner-Latest": "0.4.0"},
            request=httpx.Request("POST", f"http://test{url}"))

    monkeypatch.setattr(client._client, "post", fake_post)
    try:
        assert await client.claim() is None
    finally:
        await client.aclose()
    assert captured["json"]["version"] == "0.3.1"
    assert client.latest_advertised == "0.4.0"


def test_newer_available_compare(monkeypatch):
    from genesis_cli.runner import loop as loop_mod

    monkeypatch.setattr(loop_mod, "own_version", lambda: "0.3.1")
    client = RunnerClient("http://test", "grt_x")
    client.latest_advertised = "0.3.1"
    assert loop_mod._newer_available(client) is None
    client.latest_advertised = "0.10.0"
    assert loop_mod._newer_available(client) == "0.10.0"
    client.latest_advertised = None
    assert loop_mod._newer_available(client) is None
