"""`genesis` — the Genesis Agent Workbench CLI.

Commands:
  genesis adopt [path]   Audit a repo for agent-readiness: run delivery gates,
                         completeness + env analysis, score it, and draft
                         AGENTS.md. Deterministic — no API keys, no server.
  genesis gates [path]   Run just the delivery gates (no full analysis) and
                         print a per-gate table. Fast local iteration + CI.
  genesis init [path]    Scaffold a new/bare repo so agents start agent-ready:
                         AGENTS.md, .gitignore, .env.example, audit workflow,
                         gate config. Idempotent — never overwrites.
  genesis --version      Print the installed genesis-cli version.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .adopt import run_audit
from .agents_md import render_agents_md
from .baseline import append_history, compare, render_trend
from .report import render_html, render_terminal
from .sarif import render_sarif


def _version() -> str:
    """The installed genesis-cli version, single-sourced from package metadata.

    Reading the *distribution* metadata keeps pyproject the one place a version
    is written. Running from a source checkout without an install has no
    metadata to read, so fall back rather than crash `--version`.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("genesis-cli")
    except PackageNotFoundError:
        return "0+unknown (genesis-cli is not installed)"


def _cmd_adopt(args: argparse.Namespace) -> int:
    repo = Path(args.path).resolve()
    if not repo.is_dir():
        print(f"error: {repo} is not a directory", file=sys.stderr)
        return 2
    if args.fail_on_regression and not args.baseline:
        print("error: --fail-on-regression requires --baseline", file=sys.stderr)
        return 2

    report = run_audit(repo)

    comparison = None
    if args.baseline:
        # Read the baseline before any artifact is written: --baseline is often
        # the previous run's report.json at the very path we are about to write.
        baseline_path = Path(args.baseline)
        try:
            baseline = json.loads(baseline_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: cannot read baseline {baseline_path}: {exc}",
                  file=sys.stderr)
            return 2
        comparison = compare(report, baseline)
        report["baseline_comparison"] = comparison

    if args.review:
        from .review import run_review

        print("Running AI second-reader (claude, subscription — additive only)…",
              file=sys.stderr)
        report["ai_review"] = run_review(repo, report)
        if "error" in report["ai_review"]:
            print(f"AI review unavailable: {report['ai_review']['error']}",
                  file=sys.stderr)

    out_dir = Path(args.out) if args.out else repo / ".genesis-report"
    out_dir.mkdir(parents=True, exist_ok=True)
    history = append_history(out_dir, report)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    (out_dir / "report.html").write_text(render_html(report, history=history))
    (out_dir / "report.sarif").write_text(json.dumps(render_sarif(report), indent=2))
    agents_md = render_agents_md(report)
    (out_dir / "AGENTS.md").write_text(agents_md)

    if args.write:
        target = repo / "AGENTS.md"
        if target.exists() and not args.force:
            print(f"note: {target} already exists — draft left at "
                  f"{out_dir / 'AGENTS.md'} (use --force to overwrite)")
        else:
            target.write_text(agents_md)
            print(f"wrote {target}")

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_terminal(report))
        if trend := render_trend(history):
            print(trend)
        print(f"Artifacts: {out_dir}/report.html · report.json · report.sarif "
              f"· AGENTS.md")

    score = report["readiness"]["score"]
    ci_mode = args.min_score is not None or args.fail_on_regression
    if ci_mode:
        failed = False
        if args.min_score is not None and score < args.min_score:
            print(f"FAIL: readiness {score} below --min-score {args.min_score}",
                  file=sys.stderr)
            failed = True
        if args.fail_on_regression and comparison and comparison["regressed"]:
            print("FAIL: regression since baseline (score drop, new gap, or "
                  "new critical/high finding)", file=sys.stderr)
            failed = True
        return 1 if failed else 0
    # Default exit code mirrors the grade: A/B/C -> 0, D/F -> 1 (CI-friendly).
    return 0 if report["readiness"]["grade"] in ("A", "B", "C") else 1


def _cmd_gates(args: argparse.Namespace) -> int:
    from genesis_gates import GATE_REGISTRY

    from .adopt import ADOPT_GATES, run_gates
    from .gates import gates_exit_code, render_gates_terminal

    repo = Path(args.path).resolve()
    if not repo.is_dir():
        print(f"error: {repo} is not a directory", file=sys.stderr)
        return 2

    if args.gate:
        # De-duplicate while preserving the order the user asked for.
        selected = list(dict.fromkeys(args.gate))
        if unknown := [g for g in selected if g not in GATE_REGISTRY]:
            print(f"error: unknown gate(s): {', '.join(unknown)}. "
                  f"Valid gates: {', '.join(sorted(GATE_REGISTRY))}",
                  file=sys.stderr)
            return 2
    else:
        selected = list(ADOPT_GATES)

    package = run_gates(repo, selected)
    if args.json:
        print(json.dumps(package, indent=2))
    else:
        print(render_gates_terminal(package))
    return gates_exit_code(package)


def _cmd_init(args: argparse.Namespace) -> int:
    from .init import render_summary, run_init

    repo = Path(args.path).resolve()
    if repo.exists() and not repo.is_dir():
        print(f"error: {repo} is not a directory", file=sys.stderr)
        return 2
    created, skipped = run_init(repo)
    print(render_summary(repo, created, skipped))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="genesis",
        description="Genesis Agent Workbench — make any repo agent-ready and "
                    "keep AI-generated changes governed.",
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {_version()}")
    sub = parser.add_subparsers(dest="command", required=True)

    adopt = sub.add_parser(
        "adopt", help="audit a repo for agent-readiness (score, gaps, AGENTS.md)")
    adopt.add_argument("path", nargs="?", default=".", help="repo to audit (default: .)")
    adopt.add_argument("--out", help="artifact directory (default: <repo>/.genesis-report)")
    adopt.add_argument("--write", action="store_true",
                       help="write AGENTS.md into the repo root")
    adopt.add_argument("--force", action="store_true",
                       help="with --write: overwrite an existing AGENTS.md")
    adopt.add_argument("--json", action="store_true",
                       help="print the full JSON report to stdout")
    adopt.add_argument("--review", action="store_true",
                       help="add the AI second-reader pass (claude CLI, "
                            "subscription auth; additive-only — never alters "
                            "the deterministic score)")
    adopt.add_argument("--min-score", type=float, default=None,
                       help="CI mode: exit 1 if the readiness score is below "
                            "this threshold")
    adopt.add_argument("--baseline", metavar="PATH",
                       help="previous report.json to diff against; adds a "
                            "CHANGES SINCE BASELINE section and embeds "
                            "baseline_comparison in report.json")
    adopt.add_argument("--fail-on-regression", action="store_true",
                       help="CI mode (requires --baseline): exit 1 on a "
                            "regression — score drop > 0.5, a new "
                            "mandatory-flow gap, or a new critical/high "
                            "finding")
    adopt.set_defaults(func=_cmd_adopt)

    gates = sub.add_parser(
        "gates", help="run just the delivery gates (no full analysis): "
                      "per-gate table, exit 1 if any gate failed")
    gates.add_argument("path", nargs="?", default=".",
                       help="repo to check (default: .)")
    gates.add_argument("--gate", action="append", metavar="NAME",
                       help="run only this gate (repeatable); default: the "
                            "adopt gate set")
    gates.add_argument("--json", action="store_true",
                       help="print the evidence package as JSON")
    gates.set_defaults(func=_cmd_gates)

    init = sub.add_parser(
        "init", help="scaffold a new/bare repo so agents start agent-ready "
                     "(AGENTS.md, .gitignore, .env.example, audit workflow); "
                     "never overwrites existing files")
    init.add_argument("path", nargs="?", default=".",
                      help="directory to scaffold (default: .; created if missing)")
    init.set_defaults(func=_cmd_init)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
