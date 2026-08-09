"""Open-core boundary guard — the dependency arrow never reverses.

The OSS-bound packages (genesis-gates, genesis-cli) must never import from the
commercial ones (genesis-server, genesis-executor, frontend). Commercial code
may import OSS; the reverse breaks the published packages and leaks the
boundary. This test IS the enforcement — it fails the whole suite if anyone
adds a reverse import. See docs/open-core.md.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]  # …/packages/genesis-gates/tests -> repo
_OSS_SRC = [
    _REPO / "packages" / "genesis-gates" / "src",
    _REPO / "packages" / "genesis-cli" / "src",
]
_FORBIDDEN = re.compile(
    r"^\s*(from|import)\s+(genesis_server|genesis_executor)\b", re.MULTILINE
)


def test_oss_packages_never_import_commercial_code():
    violations: list[str] = []
    for root in _OSS_SRC:
        assert root.is_dir(), f"expected OSS source root missing: {root}"
        for f in root.rglob("*.py"):
            text = f.read_text(errors="replace")
            for m in _FORBIDDEN.finditer(text):
                line_no = text[: m.start()].count("\n") + 1
                violations.append(f"{f.relative_to(_REPO)}:{line_no}: {m.group(0).strip()}")
    assert not violations, (
        "Open-core boundary violated — OSS packages import commercial code:\n"
        + "\n".join(violations)
        + "\nMove the needed primitive INTO genesis-gates instead (docs/open-core.md)."
    )
