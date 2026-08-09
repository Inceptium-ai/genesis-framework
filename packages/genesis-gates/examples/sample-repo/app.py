"""Tiny sample service used to exercise the gates end-to-end.

NOTE: this file intentionally contains a planted fake secret so the secret-scan
gate has something real to find. It is not a real credential.
"""

# --- planted finding for the secret-scan gate (fake, do not use) ---
GITHUB_TOKEN = "ghp_1234567890abcdefghijklmnopqrstuvwx12"  # noqa: S105


def add(a: int, b: int) -> int:
    return a + b


def build_report(rows: list[dict]) -> dict:
    return {"count": len(rows), "total": sum(r.get("amount", 0) for r in rows)}
