"""Dependency reconciliation — the built code is ground truth (PROD-4).

Dependency detection runs at planning time and *guesses* from the spec; the
executor then builds whatever the architecture actually chose. Live testing
showed the drift: detection flagged OPENAI_API_KEY / AI_MODEL_NAME, but the
built app reads OPENROUTER_API_KEY / LLM_MODEL — so deploy shipped an empty key
and the AI feature 500'd.

After the execute stage, this pass scans the generated app for the env vars it
actually consumes and reconciles them into the project's dependency map:

- docker-compose ``${VAR}`` / ``${VAR:-default}`` references are the deploy-time
  contract (``.env.deploy`` feeds exactly these). A var with no default (or an
  empty default) and no stored value is a REQUIRED gap -> added as a dep.
- Direct code reads (os.getenv / os.environ / process.env / import.meta.env)
  are a secondary signal, reported for visibility.

A brownfield audit has no stored dependency map, so ``declared_env_vars`` reads
the repo's own ``.env.example``-family files (and a local ``.env``) and supplies
their keys as the declaration — see its docstring.
"""

from __future__ import annotations

import re
from pathlib import Path

# Vars that are infra-internal or supplied by the deploy machinery itself.
_IGNORE = {
    "SLOT", "PATH", "HOME", "NODE_ENV", "CI", "TERM", "LANG", "PORT",
    "PYTHONPATH", "PYTHONUNBUFFERED", "VIRTUAL_ENV", "HOSTNAME", "TZ",
}
_SKIP_DIRS = {".genesis-work", "node_modules", "gss", ".git", "dist", "build",
              "__pycache__", ".venv", "venv"}

_COMPOSE_VAR = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(:-([^}]*))?\}")
# `KEY=`, `export KEY=` — a declaration. Commented-out lines are deliberately
# NOT matched: a var someone remembered to mention in a comment is not declared.
_ENV_DECL = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=")
# Env files that constitute the repo's documented configuration contract:
# `.env.example` and friends, any `.env.<stage>.example`, and a local `.env`
# (a var with a real value there is supplied, not missing).
_ENV_FILE_GLOBS = (".env", ".env.example", ".env.sample", ".env.template",
                   ".env.*.example", ".env.*.sample", ".env.*.template")
_CODE_PATTERNS = (
    re.compile(r"os\.getenv\(\s*['\"]([A-Z][A-Z0-9_]*)['\"]"),
    re.compile(r"os\.environ(?:\.get)?\(\s*['\"]([A-Z][A-Z0-9_]*)['\"]"),
    re.compile(r"os\.environ\[\s*['\"]([A-Z][A-Z0-9_]*)['\"]"),
    re.compile(r"process\.env\.([A-Z][A-Z0-9_]*)"),
    re.compile(r"import\.meta\.env\.([A-Z][A-Z0-9_]*)"),
)


def scan_compose_vars(workdir: Path) -> dict[str, dict]:
    """``${VAR}`` refs from every compose file: {var: {has_default, default}}."""
    out: dict[str, dict] = {}
    for pattern in ("docker-compose*.yml", "docker-compose*.yaml", "compose*.yml"):
        for f in workdir.glob(pattern):
            for m in _COMPOSE_VAR.finditer(f.read_text(errors="replace")):
                var, default = m.group(1), m.group(3)
                if var in _IGNORE:
                    continue
                has_default = default is not None and default.strip() != ""
                prev = out.get(var)
                # A var referenced anywhere without a real default is a gap.
                if prev is None or (prev["has_default"] and not has_default):
                    out[var] = {"has_default": has_default, "default": default}
    return out


def scan_code_vars(workdir: Path) -> set[str]:
    """Env vars read directly by the generated code (secondary signal)."""
    found: set[str] = set()
    for f in workdir.rglob("*"):
        if not f.is_file() or f.suffix not in (".py", ".ts", ".tsx", ".js", ".jsx"):
            continue
        # Skip-dirs are matched RELATIVE to the repo root (an absolute path
        # may legitimately contain "build", "examples", …).
        try:
            rel = f.relative_to(workdir)
        except ValueError:
            rel = f
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for pat in _CODE_PATTERNS:
            found.update(pat.findall(text))
    return found - _IGNORE


def declared_env_vars(workdir: Path) -> dict[str, dict]:
    """Env vars the repo already declares, as a `stored_deps`-shaped mapping.

    The pipeline reconciles a *built app* against the dependency map Genesis
    stores for it. A brownfield audit (`genesis adopt`) has no such map, so the
    repo's own env files are the equivalent declaration: keys in `.env.example`
    (and `.env.sample` / `.env.template`, any `.env.<stage>.example`, plus a
    local `.env` if one exists) ARE the documented deploy contract. Without
    this, every ``${VAR}`` a compose file references without a default counts
    as an undeclared gap even when the repo documents it properly — which made
    the category measure "does this machine happen to hold values" instead of
    "are env vars declared".

    Only key NAMES are read; values are never parsed, logged or reported, so
    pointing this at a real `.env` cannot leak a secret. Commented-out lines do
    not count as declarations.
    """
    declared: dict[str, dict] = {}
    seen: set[Path] = set()
    for pattern in _ENV_FILE_GLOBS:
        for f in sorted(workdir.glob(pattern)):
            if not f.is_file() or f in seen:
                continue
            seen.add(f)
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                m = _ENV_DECL.match(line)
                if not m or m.group(1) in _IGNORE:
                    continue
                declared.setdefault(m.group(1), {
                    "env_var": m.group(1),
                    "source_detected": f"declared in {f.name}",
                })
    return declared


def reconcile(workdir: Path, stored_deps: dict) -> dict:
    """Diff the app's actual env surface against the stored dependency map.

    Returns {"missing_required": [dep dicts to add], "defaulted": [...],
    "code_only": [...]} — pure function; the caller persists.
    """
    compose = scan_compose_vars(workdir)
    code_vars = scan_code_vars(workdir)
    known = set(stored_deps or {})

    missing_required: list[dict] = []
    defaulted: list[str] = []
    for var, info in sorted(compose.items()):
        if var in known:
            continue
        if info["has_default"]:
            defaulted.append(f"{var} (default: {info['default']})")
            continue
        missing_required.append({
            "env_var": var, "type": "credential" if any(
                k in var for k in ("KEY", "SECRET", "TOKEN", "PASSWORD")) else "flag",
            "service": "Detected from built code",
            "required": True, "default_strategy": "user_must_provide",
            "default_value": None, "doc_url": None,
            "description": (
                "Referenced by the generated app's compose file with no default "
                "— added by post-build dependency reconciliation."
            ),
            "source_detected": "reconciliation",
        })

    code_only = sorted(v for v in code_vars if v not in known and v not in compose)
    return {
        "missing_required": missing_required,
        "defaulted": defaulted,
        "code_only": code_only,
    }
