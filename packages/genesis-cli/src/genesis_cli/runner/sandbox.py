"""OS-level sandbox for runner tasks.

Since 0.3.2 a task's agent may run build and test commands (node, npm,
python, …) so it can check its own work. A command is arbitrary code: without
a sandbox it runs as the user, with their whole home directory in reach. This
module contains each task with the operating system's own process sandbox —
Seatbelt (``sandbox-exec``) on macOS, Bubblewrap (``bwrap``) on Linux — with no
image to build and no daemon.

What a sandboxed task can do:
- write ONLY its task directory, a private temp directory, and the state
  directories the ``claude`` CLI and package managers need;
- read the system, the toolchain, and that same short list — the rest of the
  home directory (SSH keys, cloud credentials, other repositories, browser
  profiles, documents) is unreadable;
- reach the internet on the standard web ports only (macOS); nothing on this
  machine's localhost.

Credentials: this module never reads, stores or passes a Claude login. The
official ``claude`` CLI finds its own login exactly where it keeps it (the
macOS Keychain, or ``~/.claude`` on Linux); the sandbox simply leaves that
reachable. Passing a subscription token in through the environment — the
design other sandbox tools use — is deliberately NOT done: a personal
subscription credential must not be handled by a third-party tool.

Limits, stated plainly:
- It is a shared-kernel process sandbox, not a virtual machine.
- The agent process can use the same Claude login the CLI uses (it IS the
  CLI). The sandbox protects everything else.
- macOS: metadata (names, sizes) of paths outside the sandbox stay visible;
  contents do not. Linux: the network is not restricted in this version.

Fail closed: when a sandbox was asked for and cannot be built, the task does
not run. There is no silent fallback to running unsandboxed.
"""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass, field
from pathlib import Path


class SandboxUnavailable(RuntimeError):
    """No usable OS sandbox on this machine (the message says what to do)."""


# State the claude CLI and common package managers keep under the home
# directory. Read-write: the CLI writes sessions and refreshes its login;
# package managers write caches. Only entries that EXIST are granted.
_HOME_READ_WRITE = (
    ".claude",
    ".claude.json",
    ".claude.json.backup",
    "Library/Keychains",                 # macOS: where the CLI's login lives
    "Library/Caches/claude-cli-nodejs",
    ".npm", ".cache", ".pnpm-store",
    "Library/Caches/pip", "Library/Caches/pnpm", "Library/Caches/Yarn",
    "Library/Caches/node-gyp", "Library/Caches/uv",
)
# Toolchains installed under the home directory. Read-only.
_HOME_READ_ONLY = (
    ".local/bin", ".local/share/claude", ".local/share/uv", ".local/share/pnpm",
    ".nvm", ".volta", ".fnm", ".asdf", ".pyenv", ".rbenv", ".bun", ".deno",
    ".cargo/bin", ".rustup", ".yarn", "Library/pnpm", "go/bin",
    ".gitconfig", ".config/git",
)


@dataclass(frozen=True)
class Sandbox:
    """How to contain one task. ``workdir`` is the only project path the
    task may write; ``tmpdir`` is its private scratch space."""

    workdir: Path
    tmpdir: Path
    home: Path = field(default_factory=Path.home)
    system: str = field(default_factory=platform.system)
    # PATH as the task will see it (toolchains under home are found from it).
    path_env: str = field(default_factory=lambda: os.environ.get("PATH", ""))

    # ── what is reachable ────────────────────────────────────────────────────

    def _existing(self, names: tuple[str, ...]) -> list[Path]:
        return [self.home / n for n in names if (self.home / n).exists()]

    def writable_paths(self) -> list[Path]:
        return [self.workdir, self.tmpdir, *self._existing(_HOME_READ_WRITE)]

    def readonly_home_paths(self) -> list[Path]:
        out = self._existing(_HOME_READ_ONLY)
        for p in self._toolchain_paths():
            if p not in out:
                out.append(p)
        return out

    def _toolchain_paths(self) -> list[Path]:
        """Toolchains the user installed under their home directory, found
        from PATH: each such PATH directory, plus its install root when it is
        a ``bin`` directory (a virtualenv, an nvm/pyenv version, conda) —
        the interpreter there needs its sibling ``lib``. Never the home
        directory itself, ``~/.local`` as a whole, or a directory that is a
        git checkout."""
        home = self.home.resolve()
        found: list[Path] = []
        for entry in (self.path_env or "").split(os.pathsep):
            if not entry:
                continue
            try:
                d = Path(entry).resolve()
            except OSError:
                continue
            if not d.is_dir() or home not in d.parents:
                continue      # outside home: already readable; or is home
            candidates = [d]
            root = d.parent
            if (d.name == "bin" and root != home and root != home / ".local"
                    and home in root.parents and not (root / ".git").exists()):
                candidates.append(root)
            for c in candidates:
                if c not in found:
                    found.append(c)
        return found

    def env(self, base: dict[str, str]) -> dict[str, str]:
        """The task's environment: temp files go to the private temp dir."""
        out = dict(base)
        out["TMPDIR"] = str(self.tmpdir)
        # Claude Code keeps its own scratch space under /tmp/claude-<uid>,
        # shared by every session of this user. Point it at the task's
        # private temp dir instead of opening that shared directory up.
        out["CLAUDE_CODE_TMPDIR"] = str(self.tmpdir)
        return out

    # ── wrapping ─────────────────────────────────────────────────────────────

    def wrap(self, argv: list[str]) -> list[str]:
        """``argv`` prefixed with the sandbox launcher. Raises
        :class:`SandboxUnavailable` rather than returning ``argv`` bare."""
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        if self.system == "Darwin":
            exe = shutil.which("sandbox-exec")
            if not exe:
                raise SandboxUnavailable(
                    "macOS sandbox-exec was not found on PATH.")
            return [exe, "-p", self.seatbelt_profile(), *argv]
        if self.system == "Linux":
            exe = shutil.which("bwrap")
            if not exe:
                raise SandboxUnavailable(
                    "Bubblewrap is not installed. Install it (Debian/Ubuntu: "
                    "`sudo apt install bubblewrap`; Fedora: `sudo dnf install "
                    "bubblewrap`), or start the runner with --no-sandbox to "
                    "run tasks unsandboxed.")
            return [exe, *self.bwrap_args(), *argv]
        raise SandboxUnavailable(
            f"No task sandbox is available on {self.system}. Start the runner "
            "with --no-sandbox to run tasks unsandboxed.")

    # ── macOS ────────────────────────────────────────────────────────────────

    def seatbelt_profile(self) -> str:
        def q(p: Path | str) -> str:
            # Seatbelt matches on resolved paths (/tmp -> /private/tmp).
            return '"' + str(os.path.realpath(p)).replace("\\", "\\\\").replace('"', '\\"') + '"'

        home = q(self.home)
        rw = self.writable_paths()
        ro = self.readonly_home_paths()
        lines = [
            "(version 1)",
            "(allow default)",
            # ── files: write nothing, then grant the short list ──────────────
            '(deny file-write* (subpath "/"))',
            "(allow file-write*",
            *[f"  (subpath {q(p)})" if p.is_dir() else f"  (literal {q(p)})" for p in rw],
            '  (literal "/dev/null") (literal "/dev/zero") (literal "/dev/tty")',
            '  (literal "/dev/dtracehelper") (literal "/dev/random") (literal "/dev/urandom")',
            '  (regex #"^/dev/fd/") (regex #"^/dev/ttys[0-9]+$")',
            '  (literal "/dev/stdout") (literal "/dev/stderr"))',
            # ── files: the home directory is unreadable, then the same list ──
            f"(deny file-read* (subpath {home}))",
            # Path lookups must still work (a task directory lives under the
            # home directory); names and sizes are visible, contents are not.
            f"(allow file-read-metadata (subpath {home}))",
            "(allow file-read*",
            *[f"  (subpath {q(p)})" if p.is_dir() else f"  (literal {q(p)})" for p in [*rw, *ro]],
            ")",
            # ── network: web ports out, nothing local ────────────────────────
            "(deny network*)",
            '(allow network-outbound (remote tcp "*:443") (remote tcp "*:80"))',
            # Name resolution (mDNSResponder) and its unix socket.
            '(allow network-outbound (literal "/private/var/run/mDNSResponder"))',
            '(allow network-outbound (remote udp "*:53") (remote tcp "*:53"))',
            # Local services on this machine stay out of reach, whatever port.
            '(deny network-outbound (remote ip "localhost:*"))',
        ]
        return "\n".join(lines) + "\n"

    # ── Linux ────────────────────────────────────────────────────────────────

    def bwrap_args(self) -> list[str]:
        home = str(self.home)
        args = [
            "--die-with-parent", "--new-session",
            "--unshare-pid", "--unshare-ipc", "--unshare-uts",
            "--ro-bind", "/", "/",
            "--dev", "/dev", "--proc", "/proc",
            "--tmpfs", "/tmp",
            # The home directory disappears; the short list is mounted back.
            "--tmpfs", home,
        ]
        for p in self.readonly_home_paths():
            args += ["--ro-bind", str(p), str(p)]
        for p in self.writable_paths():
            args += ["--bind", str(p), str(p)]
        args += ["--setenv", "TMPDIR", str(self.tmpdir), "--chdir", str(self.workdir)]
        return args


def describe(system: str | None = None) -> str:
    """One line for the runner's startup banner."""
    system = system or platform.system()
    if system == "Darwin" and shutil.which("sandbox-exec"):
        return ("on (macOS Seatbelt): tasks can write only their task folder, "
                "cannot read the rest of your home directory, and reach the "
                "internet on web ports only")
    if system == "Linux" and shutil.which("bwrap"):
        return ("on (Bubblewrap): tasks can write only their task folder and "
                "cannot see the rest of your home directory; network is not "
                "restricted in this version")
    return "UNAVAILABLE on this machine"


def available(system: str | None = None) -> bool:
    system = system or platform.system()
    return bool((system == "Darwin" and shutil.which("sandbox-exec"))
                or (system == "Linux" and shutil.which("bwrap")))
