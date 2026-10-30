"""Headless `claude` driver — the public runner-side reimplementation of the
platform's stream-json adapter (same argv, same parsing, no shared code: this
package never imports the commercial packages).

Runs `claude -p <prompt> --output-format stream-json` as an argv subprocess
(never a shell) with cwd = the task workspace. The child env is a pass-through
of the login environment with ANTHROPIC_API_KEY explicitly UNSET so the CLI
uses the user's subscription auth and never silently bills an API key.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

# stream-json emits one JSON object per line; a single line (a large tool
# result or a long assistant turn) routinely exceeds asyncio's default 64 KB
# StreamReader line cap, which would raise LimitOverrunError mid-task. We read
# raw chunks and split on newlines ourselves (no per-line cap), and raise the
# subprocess buffer limit so the transport isn't paused every 64 KB.
_STREAM_LIMIT = 64 * 1024 * 1024  # 64 MB StreamReader buffer
_READ_CHUNK = 256 * 1024  # 256 KB per read


async def _iter_json_lines(stream: asyncio.StreamReader):
    """Yield complete decoded lines via chunked reads — an oversized line can
    never raise asyncio.LimitOverrunError."""
    buf = bytearray()
    while True:
        chunk = await stream.read(_READ_CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        while True:
            nl = buf.find(b"\n")
            if nl == -1:
                break
            line = bytes(buf[:nl])
            del buf[: nl + 1]
            yield line
    if buf:
        yield bytes(buf)


def _assistant_text(evt: dict) -> str:
    msg = evt.get("message") or {}
    parts = msg.get("content") or []
    if isinstance(parts, str):
        return parts
    return "".join(
        p.get("text", "")
        for p in parts
        if isinstance(p, dict) and p.get("type") == "text"
    )


def check_cli() -> tuple[bool, str]:
    """Preflight: is a working `claude` CLI on PATH?"""
    if not shutil.which("claude"):
        return False, (
            "The `claude` CLI is not on PATH. Install Claude Code "
            "(https://claude.com/claude-code) and sign in with your "
            "subscription, then run `genesis runner start` again."
        )
    try:
        proc = subprocess.run(["claude", "--version"], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"`claude --version` failed to run: {exc}"
    if proc.returncode != 0:
        return False, (f"`claude --version` exited {proc.returncode}: "
                       f"{(proc.stderr or proc.stdout)[:200]}")
    return True, (proc.stdout or "").strip()


class ClaudeDriver:
    """One task = one run(). kill() aborts the live subprocess (server-side
    cancel or a second Ctrl-C)."""

    def __init__(self, model: str | None = None):
        self.model = model
        self._proc: asyncio.subprocess.Process | None = None
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        if self._proc is not None and self._proc.returncode is None:
            self._proc.kill()

    def _child_env(self) -> dict:
        env = dict(os.environ)
        # Force subscription auth: never let an API key take over accidentally.
        env.pop("ANTHROPIC_API_KEY", None)
        return env

    async def run(self, prompt: str, *, workdir: Path,
                  resume_session: str | None = None,
                  on_text: Callable[[str], None] | None = None) -> dict:
        """Run one task; returns {status, session_id, usage, error} parsed
        from the CLI's final result message."""
        started = time.monotonic()
        argv = [
            "claude", "-p", prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--add-dir", str(workdir),
            "--permission-mode", "acceptEdits",
        ]
        if self.model:
            argv += ["--model", self.model]
        if resume_session:
            argv += ["--resume", resume_session]

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(workdir),
                env=self._child_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=_STREAM_LIMIT,
            )
        except FileNotFoundError:
            return {"status": "failed", "session_id": None,
                    "usage": {}, "error": "claude CLI not found"}
        self._proc = proc

        usage: dict = {"input_tokens": 0, "output_tokens": 0, "cost_usd": None,
                       "estimated": False, "duration_s": None}
        session_id: str | None = None
        status = "failed"
        error: str | None = None

        assert proc.stdout is not None
        try:
            async for raw in _iter_json_lines(proc.stdout):
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = evt.get("type")
                if evt.get("session_id"):
                    session_id = evt["session_id"]
                if etype == "assistant":
                    text = _assistant_text(evt)
                    if text and on_text is not None:
                        on_text(text)
                elif etype == "result":
                    if evt.get("total_cost_usd") is not None:
                        usage["cost_usd"] = float(evt["total_cost_usd"])
                    u = evt.get("usage") or {}
                    usage["input_tokens"] = int(u.get("input_tokens", 0))
                    usage["output_tokens"] = int(u.get("output_tokens", 0))
                    status = "completed" if evt.get("subtype") == "success" else "failed"
                    if evt.get("is_error"):
                        error = str(evt.get("result", "cli error"))[:500]
            await proc.wait()
        except asyncio.CancelledError:
            proc.kill()
            raise
        finally:
            self._proc = None

        if self.killed:
            status = "interrupted"
            error = error or "task aborted on the runner"
        elif proc.returncode not in (0, None) and status != "completed":
            status = "failed"
            if error is None:
                err = (await proc.stderr.read()).decode("utf-8", "replace") \
                    if proc.stderr else ""
                error = err[:500] or f"claude exited {proc.returncode}"

        usage["duration_s"] = time.monotonic() - started
        return {"status": status, "session_id": session_id,
                "usage": usage, "error": error}
