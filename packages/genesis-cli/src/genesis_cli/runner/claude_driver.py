"""Headless `claude` driver — the public runner-side reimplementation of the
platform's stream-json adapter (same argv, same parsing, no shared code: this
package never imports the commercial packages).

Runs `claude -p <prompt> --output-format stream-json` as an argv subprocess
(never a shell) with cwd = the task workspace. The child env is a pass-through
of the login environment with ANTHROPIC_API_KEY explicitly UNSET so the CLI
uses the user's subscription auth and never silently bills an API key.

Planning completions (0.3.0) use the same CLI in ONE-SHOT print mode —
`claude -p <prompt> --output-format json --system-prompt <system> --tools ""
--no-session-persistence` — run in a private empty directory with all tools
disabled: a pure text-in/text-out LLM call, parsed from the single result
JSON object (any non-JSON wrapper noise stripped).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
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


def _flatten_messages(messages: list[dict]) -> str:
    """One-shot print mode takes a single prompt, so a conversation (e.g. the
    server's schema-repair retry: user, assistant, user) is flattened with
    role tags. A single user message passes through untagged."""
    if len(messages) == 1 and messages[0].get("role") == "user":
        return str(messages[0].get("content") or "")
    parts = []
    for m in messages:
        role = str(m.get("role") or "user").upper()
        parts.append(f"[{role}]\n{m.get('content') or ''}")
    return "\n\n".join(parts)


def _final_json_object(stdout: str) -> dict | None:
    """The result object from `--output-format json` stdout, tolerating any
    non-JSON wrapper lines (update notices, MOTD) before or after it."""
    text = stdout.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # Line-wise, last parseable object wins (the result is printed last).
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    # Last resort: scan for embedded objects; the last decodable one wins.
    dec = json.JSONDecoder()
    best: dict | None = None
    i = text.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
            continue
        if isinstance(obj, dict):
            best = obj
        i = text.find("{", end)
    return best


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

    async def run_completion(self, *, system: str | None, messages: list[dict],
                             max_tokens: int = 4096,
                             timeout_s: float = 150.0) -> dict:
        """One planning completion (T-047): run `claude` in one-shot print
        mode and return {status, text, usage, error}. Verified invocation:
        `claude -p <prompt> --output-format json --system-prompt <system>
        --tools "" --no-session-persistence` — a single result JSON object on
        stdout whose `result` field is the model's text. Tools are disabled
        and cwd is a throwaway empty directory: a pure LLM call, nothing on
        this machine is read or written. The CLI exposes no max-tokens flag;
        the server bounds output through the prompt contract instead.
        """
        del max_tokens  # accepted for wire-payload symmetry; see docstring
        started = time.monotonic()
        prompt = _flatten_messages(messages)
        argv = [
            "claude", "-p", prompt,
            "--output-format", "json",
            "--tools", "",
            "--no-session-persistence",
        ]
        if system:
            argv += ["--system-prompt", system]
        if self.model:
            argv += ["--model", self.model]

        usage: dict = {"input_tokens": 0, "output_tokens": 0, "duration_s": None}
        with tempfile.TemporaryDirectory(prefix="genesis-completion-") as empty:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=empty,
                    env=self._child_env(),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=_STREAM_LIMIT,
                )
            except FileNotFoundError:
                return {"status": "failed", "text": None, "usage": usage,
                        "error": "claude CLI not found"}
            self._proc = proc
            try:
                out_b, err_b = await asyncio.wait_for(proc.communicate(),
                                                      timeout=timeout_s)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return {"status": "failed", "text": None, "usage": usage,
                        "error": f"completion timed out after {timeout_s:.0f}s"}
            except asyncio.CancelledError:
                proc.kill()
                raise
            finally:
                self._proc = None

        usage["duration_s"] = time.monotonic() - started
        if self.killed:
            return {"status": "interrupted", "text": None, "usage": usage,
                    "error": "completion aborted on the runner"}

        evt = _final_json_object(out_b.decode("utf-8", "replace"))
        if evt is None:
            err = err_b.decode("utf-8", "replace")[:500]
            return {"status": "failed", "text": None, "usage": usage,
                    "error": err or f"claude exited {proc.returncode} "
                                    "with no result JSON"}
        u = evt.get("usage") or {}
        usage["input_tokens"] = int(u.get("input_tokens") or 0)
        usage["output_tokens"] = int(u.get("output_tokens") or 0)
        text = evt.get("result")
        if evt.get("is_error") or evt.get("subtype") != "success" \
                or not isinstance(text, str):
            return {"status": "failed", "text": None, "usage": usage,
                    "error": str(evt.get("result") or "cli error")[:500]}
        return {"status": "completed", "text": text, "usage": usage,
                "error": None}
