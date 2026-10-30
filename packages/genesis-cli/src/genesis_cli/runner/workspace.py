"""Local task workspaces: safe extraction, change manifests, result archives.

The server's archive is data from a machine seam — extraction validates every
member (relative, no '..', regular files only) and uses the tarfile "data"
filter. The change manifest (sha256 per file) brackets the agent run; the
diff decides what goes back up. The same exclusion set applies in every
direction: VCS internals, dependency trees, caches, env files.
"""

from __future__ import annotations

import hashlib
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

_EXCLUDE_DIRS = {".git", "node_modules", ".genesis-work", "__pycache__",
                 ".venv", "dist", "build"}


class ExtractError(ValueError):
    """An archive member tried to escape or smuggle a non-file."""


def _excluded(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return any(p in _EXCLUDE_DIRS or p.startswith(".env") for p in parts)


def safe_extract(tar_path: Path, dest: Path) -> list[str]:
    """Extract the workspace archive; every member is validated first.
    Returns the extracted member names."""
    dest.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    with tarfile.open(tar_path, mode="r:gz") as tar:
        members = tar.getmembers()
        for m in members:
            p = PurePosixPath(m.name)
            if p.is_absolute() or ".." in p.parts or not p.parts:
                raise ExtractError(f"unsafe archive member path: {m.name!r}")
            if not m.isreg():
                raise ExtractError(f"archive member {m.name!r} is not a regular file")
        tar.extractall(path=dest, members=members, filter="data")
        names = [m.name for m in members]
    return names


def manifest(dest: Path) -> dict[str, str]:
    """sha256 per file under dest, excluded trees skipped — the before/after
    bracket around a task run."""
    out: dict[str, str] = {}
    for path in sorted(dest.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(dest).as_posix()
        if _excluded(rel):
            continue
        h = hashlib.sha256()
        try:
            with path.open("rb") as f:
                while chunk := f.read(1024 * 1024):
                    h.update(chunk)
        except OSError:
            continue
        out[rel] = h.hexdigest()
    return out


def diff(before: dict[str, str], after: dict[str, str]) -> tuple[list[str], list[str]]:
    """(changed, deleted): new or modified paths, and paths that vanished."""
    changed = [p for p, digest in after.items() if before.get(p) != digest]
    deleted = [p for p in before if p not in after]
    return sorted(changed), sorted(deleted)


def build_result_tar(dest: Path, changed: list[str]) -> Path:
    """tar.gz of the changed files (relative to dest) in a temp file the
    caller uploads then deletes. Excluded paths never ride along."""
    fd = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
    tar_path = Path(fd.name)
    with tarfile.open(fileobj=fd, mode="w:gz") as tar:
        for rel in changed:
            if _excluded(rel):
                continue
            full = dest / rel
            if not full.is_file() or full.is_symlink():
                continue
            tar.add(str(full), arcname=rel, recursive=False)
    fd.close()
    return tar_path
