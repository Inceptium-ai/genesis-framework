"""The runner loop: connect, start, status.

start() is the long-lived process: claim → materialize the workspace →
drive the local `claude` CLI (events batched upstream ~1/s; the batch reply
carries the server's cancel signal) → diff → upload the result → clean up.

Ctrl-C is graceful: the first stops claiming and lets the current task
finish; the second aborts the task (the server records it interrupted).
"""

from __future__ import annotations

import asyncio
import getpass
import platform
import re
import shutil
import sys

import httpx

from .claude_driver import ClaudeDriver, check_cli
from .client import RunnerAuthError, RunnerClient
from .config import config_path, load_config, save_config, work_root
from .workspace import build_result_tar, diff, manifest, safe_extract

_EVENT_FLUSH_S = 1.0
_CLAIM_BACKOFF_S = 5.0
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _say(msg: str) -> None:
    print(msg, flush=True)


# ── connect ───────────────────────────────────────────────────────────────────

def connect(server: str, name: str | None = None) -> int:
    server = server.strip().rstrip("/")
    if not server.startswith(("http://", "https://")):
        print("error: --server must be an http(s) URL", file=sys.stderr)
        return 2
    name = (name or platform.node() or "my-machine").strip()[:80]
    _say(f"Connecting this machine to {server} as runner {name!r}.")
    _say("In Genesis: Settings → Runners → New runner, then paste the grt_ "
         "token below (it is shown there exactly once).")
    try:
        token = getpass.getpass("Runner token: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\naborted", file=sys.stderr)
        return 2
    if not token:
        print("error: no token provided", file=sys.stderr)
        return 2

    async def _probe() -> bool:
        client = RunnerClient(server, token)
        try:
            return await client.probe()
        finally:
            await client.aclose()

    try:
        ok = asyncio.run(_probe())
    except httpx.HTTPError as exc:
        print(f"error: could not reach {server}: {exc}", file=sys.stderr)
        return 1
    if not ok:
        print("error: the server rejected that token — mint a fresh one in "
              "Settings → Runners and try again.", file=sys.stderr)
        return 1
    path = save_config(server, token, name)
    _say(f"Connected. Credentials saved to {path} (mode 0600).")
    _say("Start picking up work with: genesis runner start")
    return 0


# ── status ────────────────────────────────────────────────────────────────────

def status() -> int:
    cfg = load_config()
    if not cfg.get("server") or not cfg.get("token"):
        _say("Not connected. Run: genesis runner connect --server <URL>")
        return 1
    _say(f"Server : {cfg['server']}")
    _say(f"Name   : {cfg.get('name', '?')}")
    _say(f"Config : {config_path()}")
    ok, detail = check_cli()
    _say(f"claude : {detail if ok else 'MISSING — ' + detail}")

    async def _probe() -> bool:
        client = RunnerClient(cfg["server"], cfg["token"])
        try:
            return await client.probe()
        finally:
            await client.aclose()

    try:
        auth_ok = asyncio.run(_probe())
    except httpx.HTTPError as exc:
        _say(f"Auth   : unreachable ({exc})")
        return 1
    _say(f"Auth   : {'ok' if auth_ok else 'REJECTED — reconnect with a fresh token'}")
    return 0 if (ok and auth_ok) else 1


# ── start ─────────────────────────────────────────────────────────────────────

class _Stop:
    """Two-stage Ctrl-C: 1 = stop claiming, finish the task; 2 = abort it."""

    def __init__(self) -> None:
        self.stopping = False
        self.driver: ClaudeDriver | None = None

    def interrupt(self) -> None:
        if not self.stopping:
            self.stopping = True
            _say("\nStopping — finishing the current task (Ctrl-C again to abort it).")
        elif self.driver is not None:
            _say("\nAborting the current task.")
            self.driver.kill()


def start() -> int:
    cfg = load_config()
    if not cfg.get("server") or not cfg.get("token"):
        print("error: not connected — run `genesis runner connect --server <URL>` "
              "first.", file=sys.stderr)
        return 2
    ok, detail = check_cli()
    if not ok:
        print(f"error: {detail}", file=sys.stderr)
        return 2
    _say(f"claude CLI: {detail}")
    try:
        return asyncio.run(_start(cfg))
    except KeyboardInterrupt:
        return 0


async def _start(cfg: dict) -> int:
    import signal

    client = RunnerClient(cfg["server"], cfg["token"])
    stop = _Stop()
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, stop.interrupt)
    except (NotImplementedError, RuntimeError):
        pass  # non-Unix: KeyboardInterrupt still lands in start()

    _say(f"Runner {cfg.get('name', '?')!r} polling {cfg['server']} for work. Ctrl-C to stop.")
    try:
        while not stop.stopping:
            try:
                item = await client.claim()
            except RunnerAuthError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            except httpx.HTTPError as exc:
                _say(f"server unreachable ({exc.__class__.__name__}); retrying in "
                     f"{_CLAIM_BACKOFF_S:.0f}s…")
                await asyncio.sleep(_CLAIM_BACKOFF_S)
                continue
            if item is None:
                continue
            await _run_item(client, item, stop)
    finally:
        await client.aclose()
    _say("Runner stopped.")
    return 0


async def _run_item(client: RunnerClient, item: dict, stop: _Stop) -> None:
    item_id = str(item.get("item_id"))
    task = item.get("task") or {}
    task_id = str(task.get("task_id") or "task")
    project = _SAFE_NAME.sub("-", str(task.get("project_name") or "project")) or "project"
    workdir = work_root() / project / item_id
    workdir.mkdir(parents=True, exist_ok=True)
    tar_in = workdir.parent / f"{item_id}.in.tar.gz"
    tar_out = None
    _say(f"▶ {task_id} (item {item_id[:12]}…) — downloading workspace")

    events: list[dict] = []

    def on_text(text: str) -> None:
        events.append({"type": "agent_message", "data": {"text": text}})

    driver = ClaudeDriver()
    stop.driver = driver

    async def flusher() -> None:
        while True:
            await asyncio.sleep(_EVENT_FLUSH_S)
            # An empty batch still flushes: it doubles as the cancel poll.
            batch, events[:] = list(events), []
            try:
                cancelled = await client.post_events(item_id, batch)
            except (httpx.HTTPError, RunnerAuthError):
                continue  # transient — the final result upload is what matters
            if cancelled:
                _say(f"✖ {task_id} cancelled by the server — stopping claude")
                driver.kill()
                return

    try:
        await client.download_workspace(item_id, tar_in)
        safe_extract(tar_in, workdir)
        before = manifest(workdir)

        flush_task = asyncio.create_task(flusher())
        try:
            result = await driver.run(
                str(task.get("prompt") or ""),
                workdir=workdir,
                resume_session=task.get("resume_session") or None,
                on_text=on_text,
            )
        finally:
            flush_task.cancel()
        # Final event flush (best-effort).
        if events:
            try:
                await client.post_events(item_id, list(events))
            except (httpx.HTTPError, RunnerAuthError):
                pass
            events.clear()

        after = manifest(workdir)
        changed, deleted = diff(before, after)
        payload = {
            "status": result.get("status", "failed"),
            "session_id": result.get("session_id"),
            "error": result.get("error"),
            "usage": result.get("usage") or {},
            "deleted": deleted,
        }
        if changed:
            tar_out = build_result_tar(workdir, changed)
        await client.post_result(item_id, payload, tar_out)
        _say(f"✓ {task_id}: {payload['status']} "
             f"({len(changed)} changed, {len(deleted)} deleted)")
    except RunnerAuthError:
        raise
    except Exception as exc:  # noqa: BLE001 — report, never crash the loop
        _say(f"✗ {task_id}: {exc.__class__.__name__}: {exc}")
        try:
            await client.post_result(item_id, {
                "status": "failed", "session_id": None,
                "error": f"runner error: {exc}"[:500],
                "usage": {}, "deleted": [],
            })
        except (httpx.HTTPError, RunnerAuthError):
            pass
    finally:
        stop.driver = None
        tar_in.unlink(missing_ok=True)
        if tar_out is not None:
            tar_out.unlink(missing_ok=True)
        shutil.rmtree(workdir, ignore_errors=True)
