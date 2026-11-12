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

from .claude_driver import ClaudeDriver, allowed_tools, check_cli
from .client import RunnerAuthError, RunnerClient, own_version
from .config import config_path, load_config, save_config, work_root
from .workspace import build_result_tar, diff, manifest, safe_extract, shipped_dirs

_EVENT_FLUSH_S = 1.0
_CLAIM_BACKOFF_S = 5.0
_NUDGE_EVERY_S = 3600.0
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _say(msg: str) -> None:
    print(msg, flush=True)


def _ver_tuple(v: str | None) -> tuple[int, ...]:
    out: list[int] = []
    for part in (v or "").split(".")[:4]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


def _newer_available(client: RunnerClient) -> str | None:
    """The advertised version, when it beats what's running here."""
    latest = client.latest_advertised
    if latest and _ver_tuple(latest) > _ver_tuple(own_version()):
        return latest
    return None


# ── self-update ──────────────────────────────────────────────────────────────

def update() -> int:
    """Upgrade genesis-cli in place: pipx when it manages this install,
    else pip. Only ever upgrades THIS package from PyPI — the server can
    name a version, never a package or URL."""
    import subprocess

    if shutil.which("pipx"):
        try:
            listing = subprocess.run(["pipx", "list", "--short"],
                                     capture_output=True, text=True,
                                     timeout=60).stdout
        except Exception:  # noqa: BLE001 — fall through to pip
            listing = ""
        if "genesis-cli" in listing:
            rc = subprocess.run(["pipx", "upgrade", "genesis-cli"]).returncode
            if rc == 0:
                _say("genesis-cli upgraded (pipx). Restart the runner to use it.")
            return rc
    rc = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade",
                         "genesis-cli"]).returncode
    if rc == 0:
        _say("genesis-cli upgraded (pip). Restart the runner to use it.")
    return rc


def _self_update_and_reexec(latest: str) -> None:
    """--auto-update, between work items only: upgrade and re-exec this same
    command line so the new code takes over with the same flags."""
    import os

    before = own_version()
    _say(f"auto-update: upgrading genesis-cli to {latest} …")
    if update() != 0:
        _say("auto-update failed — continuing on the current version.")
        return
    after = own_version()
    if _ver_tuple(after) <= _ver_tuple(before):
        # The server advertised a version the package index doesn't serve yet
        # (deploy landed before the release). Re-exec'ing here would restart
        # into the same code and loop forever — keep running instead.
        _say(f"auto-update: {latest} is not installable yet (still on "
             f"{after or 'unknown'}) — will retry later.")
        return
    _say("auto-update: restarting the runner …")
    os.execvp(sys.argv[0], sys.argv)


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
    # Planning routing: the capability is advertised on every claim; there is
    # no cheap server query for the ack, so report the client side honestly.
    from .client import CAPABILITIES

    if "completion" in CAPABILITIES:
        _say("Planning: this runner advertises 'completion' — while "
             "`genesis runner start` is running, the server routes your "
             "planning calls here (needs a 0.3.0+ server; older servers "
             "ignore it and use builds only).")
    return 0 if (ok and auth_ok) else 1


# ── start ─────────────────────────────────────────────────────────────────────

class _Stop:
    """Two-stage Ctrl-C: 1 = stop claiming, finish the task; 2 = abort it."""

    def __init__(self) -> None:
        self.stopping = False
        self.driver: ClaudeDriver | None = None
        # False = edit-only agents (`genesis runner start --no-commands`).
        self.allow_commands = True
        # True = every claude process runs inside the OS sandbox.
        self.sandboxed = False

    def interrupt(self) -> None:
        if not self.stopping:
            self.stopping = True
            _say("\nStopping — finishing the current task (Ctrl-C again to abort it).")
        elif self.driver is not None:
            _say("\nAborting the current task.")
            self.driver.kill()


def start(auto_update: bool = False, allow_commands: bool = True,
          sandbox: bool = True) -> int:
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
    from . import sandbox as sandbox_mod

    if sandbox and not sandbox_mod.available():
        # Fail closed: never quietly run tasks without the containment the
        # user was promised.
        print("error: tasks run inside an OS sandbox by default, and none is "
              "available on this machine.\n"
              "  Linux: install Bubblewrap (Debian/Ubuntu: `sudo apt install "
              "bubblewrap`; Fedora: `sudo dnf install bubblewrap`).\n"
              "  Or start with --no-sandbox to run tasks directly as your "
              "user — they can then read and write anything you can.",
              file=sys.stderr)
        return 2
    if sandbox:
        _say(f"sandbox: {sandbox_mod.describe()}")
    else:
        _say("sandbox: OFF (--no-sandbox) — tasks run directly as your user "
             "and can read and write anything you can.")
    try:
        return asyncio.run(_start(cfg, auto_update=auto_update,
                                  allow_commands=allow_commands,
                                  sandbox=sandbox))
    except KeyboardInterrupt:
        return 0


async def _start(cfg: dict, auto_update: bool = False,
                 allow_commands: bool = True, sandbox: bool = True) -> int:
    import signal
    import time as _time

    client = RunnerClient(cfg["server"], cfg["token"])
    stop = _Stop()
    stop.allow_commands = allow_commands
    stop.sandboxed = sandbox
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, stop.interrupt)
    except (NotImplementedError, RuntimeError):
        pass  # non-Unix: KeyboardInterrupt still lands in start()

    _say(f"Runner {cfg.get('name', '?')!r} polling {cfg['server']} for work. Ctrl-C to stop.")
    last_nudge = 0.0
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
            # Update handling, BETWEEN items only (never mid-task): with
            # --auto-update, upgrade + re-exec; otherwise an hourly nudge.
            latest = _newer_available(client)
            due = (last_nudge == 0.0
                   or _time.monotonic() - last_nudge > _NUDGE_EVERY_S)
            if latest and item is None and due:
                if auto_update:
                    # Returns only when it could not upgrade; the hourly
                    # cooldown then applies to retries as well as nudges.
                    _self_update_and_reexec(latest)
                    last_nudge = _time.monotonic()
                else:
                    _say(f"⬆ genesis-cli {latest} is available (you run "
                         f"{own_version() or 'unknown'}) — run "
                         "`genesis runner update`, or start with --auto-update.")
                    last_nudge = _time.monotonic()
            if item is None:
                continue
            if item.get("kind") == "completion":
                await _run_completion_item(client, item, stop)
            else:
                await _run_item(client, item, stop)
    finally:
        await client.aclose()
    _say("Runner stopped.")
    return 0


async def _run_completion_item(client: RunnerClient, item: dict,
                               stop: _Stop) -> None:
    """One planning completion (0.3.0): no workspace in either direction —
    run the local `claude` in one-shot print mode and post the text back."""
    item_id = str(item.get("item_id"))
    task = item.get("task") or {}
    _say(f"▶ planning call (item {item_id[:12]}…) — running claude one-shot")
    # The server names the model (claude CLI alias — planning defaults to the
    # latest Opus); absent (older server), "opus" rather than the machine's
    # personal claude default.
    driver = ClaudeDriver(model=str(task.get("model") or "opus"),
                          sandboxed=stop.sandboxed, sandbox_root=work_root())
    stop.driver = driver
    try:
        result = await driver.run_completion(
            system=str(task.get("system") or "") or None,
            messages=list(task.get("messages") or []),
            max_tokens=int(task.get("max_tokens") or 4096),
        )
    except Exception as exc:  # noqa: BLE001 — report, never crash the loop
        result = {"status": "failed", "text": None, "usage": {},
                  "error": f"runner error: {exc}"[:500]}
    finally:
        stop.driver = None
    payload = {
        "status": result.get("status", "failed"),
        "text": result.get("text"),
        "session_id": None,
        "error": result.get("error"),
        "usage": result.get("usage") or {},
        "deleted": [],
    }
    try:
        await client.post_result(item_id, payload)
        _say(f"✓ planning call: {payload['status']}")
    except (httpx.HTTPError, RunnerAuthError) as exc:
        _say(f"✗ planning call: result upload failed ({exc.__class__.__name__})")


async def _run_item(client: RunnerClient, item: dict, stop: _Stop) -> None:
    item_id = str(item.get("item_id"))
    task = item.get("task") or {}
    task_id = str(task.get("task_id") or "task")
    project = _SAFE_NAME.sub("-", str(task.get("project_name") or "project")) or "project"
    # ONE directory per task, reused across repair rounds: claude keys its
    # sessions by working directory, so a per-item directory made every
    # `--resume` fail with "No conversation found" and every repair round die
    # in a second. The tree itself is replaced each round — the server's
    # archive is the truth.
    workdir = work_root() / project / ("task-" + (_SAFE_NAME.sub("-", task_id) or "task"))
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    tar_in = workdir.parent / f"{item_id}.in.tar.gz"
    tar_out = None
    _say(f"▶ {task_id} (item {item_id[:12]}…) — downloading workspace")

    events: list[dict] = []

    def on_text(text: str) -> None:
        events.append({"type": "agent_message", "data": {"text": text}})

    # The server names the model (claude CLI alias — builds default to the
    # latest Sonnet); absent (older server), "sonnet" rather than the
    # machine's personal claude default.
    driver = ClaudeDriver(
        model=str(task.get("model") or "sonnet"),
        commands=(allowed_tools(task.get("allowed_commands"))
                  if stop.allow_commands else None),
        sandboxed=stop.sandboxed, sandbox_root=workdir.parent,
    )
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
        keep = shipped_dirs(safe_extract(tar_in, workdir))
        before = manifest(workdir, keep)

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

        after = manifest(workdir, keep)
        changed, deleted = diff(before, after)
        payload = {
            "status": result.get("status", "failed"),
            "session_id": result.get("session_id"),
            "error": result.get("error"),
            "usage": result.get("usage") or {},
            "deleted": deleted,
        }
        if changed:
            tar_out = build_result_tar(workdir, changed, keep)
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
        shutil.rmtree(workdir.parent / (".tmp-" + workdir.name), ignore_errors=True)
