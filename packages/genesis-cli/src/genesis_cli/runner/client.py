"""HTTP client for the Genesis runner gateway.

One rule above all: the token is a credential — it goes into the
Authorization header and NOWHERE else (never a log line, never an error
message, never a URL).
"""

from __future__ import annotations

from pathlib import Path

import httpx

# The server long-polls a claim for ~25s; read must comfortably outlast that.
_CLAIM_TIMEOUT = httpx.Timeout(10.0, read=60.0)
_IO_TIMEOUT = httpx.Timeout(10.0, read=300.0)

# What this client can serve, advertised on every claim (0.3.0+). 'completion'
# = one-shot planning LLM calls through the local `claude` CLI, no workspace.
# Additive wire change: an 0.2.0 server has no body on /claim and ignores it;
# an 0.3.0 server hands completion items only to claims that advertise it.
CAPABILITIES = ("completion",)


class RunnerAuthError(RuntimeError):
    """The server rejected the runner token (bad, rotated, or revoked)."""


class RunnerClient:
    def __init__(self, server: str, token: str):
        self._client = httpx.AsyncClient(
            base_url=server.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=_IO_TIMEOUT,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _check_auth(resp: httpx.Response) -> None:
        if resp.status_code == 401:
            raise RunnerAuthError(
                "The server rejected this runner's token — it may have been "
                "rotated or revoked. Run `genesis runner connect` again."
            )

    async def probe(self) -> bool:
        """Side-effect-free auth check: a nonexistent work item reads 404 with
        a valid token, 401 with a bad one (and never claims real work)."""
        resp = await self._client.get("/api/runner/work/probe/workspace")
        if resp.status_code == 401:
            return False
        return resp.status_code == 404

    async def claim(self) -> dict | None:
        """One long-poll. None = no work this window (poll again)."""
        resp = await self._client.post(
            "/api/runner/claim", json={"capabilities": list(CAPABILITIES)},
            timeout=_CLAIM_TIMEOUT,
        )
        self._check_auth(resp)
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) and data.get("item_id") else None

    async def download_workspace(self, item_id: str, dest: Path) -> Path:
        """Stream the task workspace archive to dest (tar.gz)."""
        async with self._client.stream(
            "GET", f"/api/runner/work/{item_id}/workspace", timeout=_IO_TIMEOUT
        ) as resp:
            self._check_auth(resp)
            resp.raise_for_status()
            with dest.open("wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)
        return dest

    async def post_events(self, item_id: str, events: list[dict]) -> bool:
        """Forward an event batch; the reply carries the cancel signal."""
        resp = await self._client.post(
            f"/api/runner/work/{item_id}/events", json={"events": events}
        )
        self._check_auth(resp)
        resp.raise_for_status()
        try:
            return bool(resp.json().get("cancelled"))
        except ValueError:
            return False

    async def post_result(self, item_id: str, result: dict,
                          tar_path: Path | None = None) -> None:
        """Upload the task outcome (+ the changed-files archive when any)."""
        import json

        data = {"result": json.dumps(result)}
        if tar_path is not None:
            with tar_path.open("rb") as f:
                resp = await self._client.post(
                    f"/api/runner/work/{item_id}/result", data=data,
                    files={"files": ("files.tar.gz", f, "application/gzip")},
                    timeout=_IO_TIMEOUT,
                )
        else:
            resp = await self._client.post(
                f"/api/runner/work/{item_id}/result", data=data, timeout=_IO_TIMEOUT
            )
        self._check_auth(resp)
        resp.raise_for_status()
