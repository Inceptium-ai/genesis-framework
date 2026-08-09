"""Brownfield repo introspection — build analysis inputs from any codebase.

Powers ``genesis adopt``: no Genesis planning artifacts exist, so markers come
from dependency manifests + configs, and the implementation surface comes from
route/endpoint definitions extracted from source. Deterministic (no LLM).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .completeness import CompletenessInput

_SKIP_DIRS = {".git", "node_modules", "dist", "build", "__pycache__", ".venv",
              "venv", ".next", "coverage", ".genesis-work", "vendor", "target",
              ".tox", ".mypy_cache", ".pytest_cache", "site-packages",
              # Vendored reference/template trees: their manifests describe
              # example apps, not this repo — they must not activate profiles.
              "gss", "examples", "fixtures", "templates"}

_MANIFEST_FILES = ("package.json", "requirements.txt", "pyproject.toml",
                   "Pipfile", "go.mod", "Gemfile", "composer.json", "pom.xml",
                   "build.gradle", "Cargo.toml")
_CONFIG_HINTS = ("docker-compose.yml", "docker-compose.yaml", "Dockerfile",
                 ".env.example", "keycloak.json", "auth_config.py",
                 # API contracts describe the system in the same way the
                 # pipeline's planning artifacts do — strong marker signals
                 # (auth schemes, multipart uploads, payment endpoints).
                 "openapi.yaml", "openapi.yml", "openapi.json", "swagger.yaml",
                 "swagger.json")

# Route/endpoint definitions across common frameworks — the implementation
# surface for completeness checks. Each pattern captures (method, path); the
# method is kept in the surface text so method-shaped flows (e.g. a DELETE
# route satisfying a delete_flow check) are visible, not just the URL.
_ROUTE_PATTERNS = (
    # FastAPI / Flask / Django-ninja style decorators
    re.compile(r"@\w+\.(get|post|put|delete|patch|route)\(\s*['\"]([^'\"]+)"),
    # Express / Koa / NestJS-ish
    re.compile(r"\b(?:app|router)\.(get|post|put|delete|patch|use)\(\s*['\"]([^'\"]+)"),
    # Django urls.py
    re.compile(r"\b(path)\(\s*['\"]([^'\"]+)"),
    # Rails routes.rb
    re.compile(r"\b(get|post|put|delete|patch|resources?)\s+['\":]([\w/\-_]+)"),
)

_SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".rb", ".go", ".java"}
_MAX_FILE_BYTES = 400_000
_MAX_FILES = 4000


def _iter_source_files(repo: Path):
    count = 0
    for f in repo.rglob("*"):
        if count >= _MAX_FILES:
            return
        if not f.is_file() or f.suffix not in _SOURCE_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in f.parts):
            continue
        try:
            if f.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        count += 1
        yield f


def marker_text(repo: Path) -> str:
    """Prose-ish signals: dependency manifests + well-known config files."""
    parts: list[str] = []
    for name in _MANIFEST_FILES + _CONFIG_HINTS:
        for f in list(repo.rglob(name))[:20]:
            if any(part in _SKIP_DIRS for part in f.parts):
                continue
            try:
                parts.append(f.read_text(errors="replace")[:50_000])
            except OSError:
                continue
    return "\n".join(parts)


def surface_text(repo: Path) -> tuple[str, int]:
    """Implementation surface: every route/endpoint string plus file paths that
    look like implementations (pages/, routes/, api/). Returns (text, n_routes)."""
    routes: list[str] = []
    for f in _iter_source_files(repo):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for pat in _ROUTE_PATTERNS:
            routes.extend(f"{method} {path}" for method, path in pat.findall(text))
    # Path-shaped implementation hints (Next.js pages/api, file routers).
    path_hints = [
        "/".join(f.parts[-3:])
        for f in _iter_source_files(repo)
        if any(seg in ("pages", "routes", "api", "views", "controllers")
               for seg in f.parts)
    ]
    return "\n".join(routes + path_hints), len(routes)


def repo_input(repo: Path) -> CompletenessInput:
    surf, _n = surface_text(repo)
    return CompletenessInput.build(marker_text(repo), surf)


# ── structure facts (feed the readiness score + AGENTS.md) ───────────────────

def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(errors="replace"))
    except Exception:  # noqa: BLE001
        return {}


def detect_commands(repo: Path) -> dict[str, str]:
    """Best-effort build/test/lint commands from manifests + Makefile."""
    cmds: dict[str, str] = {}
    pkg = _read_json(repo / "package.json")
    scripts = pkg.get("scripts", {}) if isinstance(pkg, dict) else {}
    for key, label in (("test", "test"), ("build", "build"), ("lint", "lint"),
                       ("typecheck", "typecheck"), ("type-check", "typecheck"),
                       ("dev", "dev")):
        if key in scripts and label not in cmds:
            cmds[label] = f"npm run {key}"
    if (repo / "pyproject.toml").exists() or (repo / "requirements.txt").exists():
        if any(repo.rglob("test_*.py")) or any(repo.rglob("*_test.py")):
            cmds.setdefault("test", "pytest")
        text = ""
        try:
            text = (repo / "pyproject.toml").read_text(errors="replace")
        except OSError:
            pass
        if "ruff" in text:
            cmds.setdefault("lint", "ruff check .")
    makefile = repo / "Makefile"
    if makefile.exists():
        try:
            targets = re.findall(r"^([a-zA-Z][\w\-]*):", makefile.read_text(errors="replace"),
                                 re.MULTILINE)
            for t in ("test", "lint", "build", "dev"):
                if t in targets:
                    cmds[t] = f"make {t}"
        except OSError:
            pass
    return cmds


def structure_facts(repo: Path) -> dict:
    """Booleans + counts the readiness score and AGENTS.md generator consume."""
    def _any(*globs: str) -> bool:
        return any(
            f for g in globs for f in repo.rglob(g)
            if not any(part in _SKIP_DIRS for part in f.parts)
        )

    _surface, n_routes = surface_text(repo)
    return {
        "has_tests": _any("test_*.py", "*_test.py", "*.test.ts", "*.test.tsx",
                          "*.spec.ts", "*.test.js"),
        "has_ci": _any(".github/workflows/*.yml", ".github/workflows/*.yaml",
                       ".gitlab-ci.yml", "azure-pipelines.yml"),
        "has_readme": (repo / "README.md").exists(),
        "has_agents_md": _any("AGENTS.md", "CLAUDE.md", ".cursorrules",
                              ".cursor/rules/*"),
        "has_env_example": _any(".env.example", ".env.sample", ".env.template"),
        "has_lockfile": _any("package-lock.json", "pnpm-lock.yaml", "yarn.lock",
                             "uv.lock", "poetry.lock", "Pipfile.lock", "go.sum",
                             "Gemfile.lock"),
        "has_dockerfile": _any("Dockerfile", "docker-compose.yml",
                               "docker-compose.yaml"),
        "has_gitignore": (repo / ".gitignore").exists(),
        "n_routes": n_routes,
        "commands": detect_commands(repo),
    }
