"""Runner workspace handling: safe extraction, manifest diffs, result tars."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest
from genesis_cli.runner.workspace import (
    ExtractError,
    build_result_tar,
    diff,
    manifest,
    safe_extract,
)


def _make_tar(tmp_path: Path, entries: dict[str, bytes], *, symlink: str | None = None) -> Path:
    path = tmp_path / "in.tar.gz"
    with tarfile.open(path, mode="w:gz") as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if symlink:
            info = tarfile.TarInfo(symlink)
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
    return path


def test_safe_extract_happy_path(tmp_path):
    tar = _make_tar(tmp_path, {"index.html": b"<h1>x</h1>", "src/app.js": b"1"})
    dest = tmp_path / "ws"
    names = safe_extract(tar, dest)
    assert set(names) == {"index.html", "src/app.js"}
    assert (dest / "src" / "app.js").read_bytes() == b"1"


@pytest.mark.parametrize("name", ["../escape.txt", "/abs.txt", "a/../../up.txt"])
def test_safe_extract_rejects_traversal(tmp_path, name):
    tar = _make_tar(tmp_path, {name: b"evil", "ok.txt": b"fine"})
    dest = tmp_path / "ws"
    with pytest.raises(ExtractError):
        safe_extract(tar, dest)
    # Fail closed: nothing extracted from a bad archive.
    assert not (dest / "ok.txt").exists()


def test_safe_extract_rejects_symlinks(tmp_path):
    tar = _make_tar(tmp_path, {"ok.txt": b"fine"}, symlink="sneaky")
    with pytest.raises(ExtractError):
        safe_extract(tar, tmp_path / "ws")


def test_manifest_diff_detects_changes_and_deletions(tmp_path):
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    (ws / "keep.txt").write_text("same")
    (ws / "edit.txt").write_text("v1")
    (ws / "sub" / "gone.txt").write_text("bye")
    before = manifest(ws)

    (ws / "edit.txt").write_text("v2")
    (ws / "new.txt").write_text("hello")
    (ws / "sub" / "gone.txt").unlink()
    after = manifest(ws)

    changed, deleted = diff(before, after)
    assert changed == ["edit.txt", "new.txt"]
    assert deleted == ["sub/gone.txt"]


def test_manifest_skips_excluded_trees(tmp_path):
    ws = tmp_path / "ws"
    for d in (".git", "node_modules", ".genesis-work", "__pycache__", ".venv",
              "dist", "build"):
        (ws / d).mkdir(parents=True)
        (ws / d / "f.txt").write_text("x")
    (ws / ".env").write_text("SECRET=1")
    (ws / ".env.local").write_text("SECRET=2")
    (ws / "app.py").write_text("print(1)")
    m = manifest(ws)
    assert set(m) == {"app.py"}


def test_result_tar_excludes_protected_paths(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "good.txt").write_text("ok")
    (ws / ".env").write_text("SECRET=1")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "x.js").write_text("x")
    # Even if the changed list names protected paths, they never ride along.
    tar_path = build_result_tar(ws, ["good.txt", ".env", "node_modules/x.js",
                                     "missing.txt"])
    try:
        with tarfile.open(tar_path, mode="r:gz") as tar:
            assert [m.name for m in tar.getmembers()] == ["good.txt"]
    finally:
        tar_path.unlink()
