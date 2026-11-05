"""argparse wiring for `genesis runner …` (heavy imports deferred to run time
so `genesis adopt` never pays for httpx)."""

from __future__ import annotations

import argparse


def _cmd_connect(args: argparse.Namespace) -> int:
    from .loop import connect

    return connect(args.server, args.name)


def _cmd_start(args: argparse.Namespace) -> int:
    from .loop import start

    return start(auto_update=args.auto_update)


def _cmd_update(args: argparse.Namespace) -> int:
    from .loop import update

    return update()


def _cmd_status(args: argparse.Namespace) -> int:
    from .loop import status

    return status()


def add_runner_parser(sub: argparse._SubParsersAction) -> None:
    runner = sub.add_parser(
        "runner",
        help="execute your Genesis builds' coding-agent tasks AND planning "
             "calls on THIS machine with your local claude CLI (subscription) "
             "— the server keeps orchestration, validation, gates, and billing",
    )
    rsub = runner.add_subparsers(dest="runner_command", required=True)

    connect = rsub.add_parser(
        "connect", help="pair this machine with a Genesis server "
                        "(paste a runner token from Settings → Runners)")
    connect.add_argument("--server", required=True, metavar="URL",
                         help="the Genesis server origin, e.g. "
                              "https://genesis.example.com")
    connect.add_argument("--name", default=None,
                         help="a name for this machine (default: hostname)")
    connect.set_defaults(func=_cmd_connect)

    start = rsub.add_parser(
        "start", help="poll for work and run tasks locally (Ctrl-C to stop; "
                      "twice to abort the current task)")
    start.add_argument(
        "--auto-update", action="store_true",
        help="when the server advertises a newer genesis-cli, upgrade (from "
             "PyPI only) between tasks and restart automatically")
    start.set_defaults(func=_cmd_start)

    update = rsub.add_parser(
        "update", help="upgrade genesis-cli in place (pipx when it manages "
                       "this install, else pip), then restart the runner")
    update.set_defaults(func=_cmd_update)

    status = rsub.add_parser("status", help="show connection, claude CLI and "
                                            "auth status")
    status.set_defaults(func=_cmd_status)
