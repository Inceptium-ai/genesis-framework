"""``command`` provider — bring-your-own tool.

Runs an arbitrary command in the repo and (optionally) parses its stdout into
findings. This is the escape hatch for any scanner Genesis does not bundle: point
it at your CLI, pick a ``parser`` if the output is a known format, and let the
findings drive the gate.

Status logic:
  * ``parser`` set and it yields findings -> ``failed``.
  * else ``fail_on_nonzero`` and the command exited non-zero -> ``failed``.
  * else -> ``passed``.
  * binary missing (never launched) -> ``skipped``.
  * ``parser`` set but output unparseable (or unknown/unsupported parser) -> ``error``.
"""
from __future__ import annotations

import time
from typing import Any

from ..engine.base import Finding, GateContext, GateResult, GateStatus, Severity
from ..engine.provider import Provider, register_provider
from ..parsers import PARSERS, UNSUPPORTED_PARSERS
from ..util import DEFAULT_TIMEOUT, run_cmd


@register_provider
class CommandProvider(Provider):
    key = "command"
    description = "Run an arbitrary BYO command; optionally parse its output into findings."

    def run(self, ctx: GateContext) -> GateResult:
        start = time.perf_counter()
        raw_cmd = self.settings.get("command")
        parser = self.settings.get("parser")
        fail_on_nonzero = bool(self.settings.get("fail_on_nonzero", False))
        timeout = int(self.settings.get("timeout", DEFAULT_TIMEOUT))

        if not raw_cmd:
            return GateResult(
                gate="",
                status=GateStatus.error,
                summary="command provider requires a `command` setting",
                error="missing `command`",
                duration_s=time.perf_counter() - start,
            )

        if parser is not None and parser not in PARSERS:
            return _parser_error(parser, start)

        cmd = raw_cmd.split() if isinstance(raw_cmd, str) else list(raw_cmd)
        res = run_cmd(cmd, cwd=ctx.repo_path, timeout=timeout)
        elapsed = lambda: time.perf_counter() - start  # noqa: E731

        if not res.launched:
            return GateResult(
                gate="",
                status=GateStatus.skipped,
                summary=f"command not available: {cmd[0]!r} not found on PATH",
                duration_s=elapsed(),
            )
        if res.timed_out:
            return GateResult(
                gate="",
                status=GateStatus.error,
                summary=f"command timed out after {timeout}s",
                error="timeout",
                duration_s=elapsed(),
            )

        findings: list[Finding] = []
        if parser is not None:
            try:
                findings = PARSERS[parser](res.stdout)
            except Exception as e:  # noqa: BLE001 - any parse failure is an `error`
                return GateResult(
                    gate="",
                    status=GateStatus.error,
                    summary=f"could not parse command output as {parser}",
                    error=f"{type(e).__name__}: {e}; stderr={res.stderr.strip()[:300]}",
                    duration_s=elapsed(),
                )

        if findings:
            worst = min(findings, key=lambda f: list(Severity).index(f.severity)).severity
            return GateResult(
                gate="",
                status=GateStatus.failed,
                summary=f"command produced {len(findings)} finding(s), worst={worst.value}",
                findings=findings,
                duration_s=elapsed(),
            )
        if fail_on_nonzero and res.rc != 0:
            return GateResult(
                gate="",
                status=GateStatus.failed,
                summary=f"command exited non-zero (rc={res.rc})",
                error=res.stderr.strip()[:300] or None,
                duration_s=elapsed(),
            )
        return GateResult(
            gate="",
            status=GateStatus.passed,
            summary=f"command exited {res.rc} with no findings",
            duration_s=elapsed(),
        )


def _parser_error(parser: Any, start: float) -> GateResult:
    if parser in UNSUPPORTED_PARSERS:
        msg = f"parser {parser!r} not yet supported"
    else:
        msg = f"unknown parser {parser!r} (known: {', '.join(sorted(PARSERS))})"
    return GateResult(
        gate="",
        status=GateStatus.error,
        summary=msg,
        error=msg,
        duration_s=time.perf_counter() - start,
    )
