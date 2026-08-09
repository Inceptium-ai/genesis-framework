"""Deterministic task risk classification — the substrate for bounded autonomy.

Policy engines decide *what may proceed without a human*; this module only
answers *how risky is this change*, from the task's own declared content
(title, story, files, contracts, validation). Deterministic and explainable:
every classification carries the markers that produced it.

Doctrine (mirrors the delivery strategy): auth, payments, schema migrations,
secrets, infrastructure, and customer-data handling are always HIGH — they wait
for a human no matter what. Docs/tests/lint/copy-only changes are LOW. Anything
else is MEDIUM. HIGH beats LOW when markers conflict.
"""

from __future__ import annotations

import re

_HIGH_MARKERS: tuple[tuple[str, str], ...] = (
    (r"\b(auth|login|sign[- _]?in|password|session|token|permission|rbac|oauth|oidc)\b",
     "auth/session/permissions"),
    (r"\b(payment|stripe|billing|refund|charge|checkout|invoice|subscription)\b",
     "payments/billing"),
    (r"\b(migration|alembic|schema change|ALTER TABLE|DROP TABLE|DROP COLUMN)\b",
     "database migration/schema"),
    (r"\b(secret|credential|api[- _]?key|private key|vault|\.env)\b",
     "secrets/credentials"),
    (r"\b(docker|compose|kubernetes|k8s|terraform|infra(structure)?|deploy(ment)? script|nginx)\b",
     "infrastructure/deploy"),
    (r"\b(delete (user|account|data)|PII|personal data|gdpr|data export|customer data)\b",
     "customer data"),
)

_LOW_MARKERS: tuple[tuple[str, str], ...] = (
    (r"\b(readme|docs?|documentation|comment(s)? only|changelog)\b", "docs-only"),
    (r"\b(unit tests? only|test coverage|add(ing)? tests)\b", "tests-only"),
    (r"\b(lint|format(ting)?|prettier|style[- ]?only|css[- ]?only|typo)\b", "style/lint"),
    (r"\b(version bump|footer|copy change|wording|label text)\b", "copy/cosmetic"),
)

_TEST_PATH = re.compile(r"(^|/)tests?/|\.test\.|\.spec\.|_test\.")
_DOC_PATH = re.compile(r"\.(md|rst|txt)$|(^|/)docs?/")


def _task_text(task: dict) -> str:
    parts = [str(task.get("title", "")), str(task.get("user_story", ""))]
    parts += [str(c) for c in task.get("acceptance_criteria") or []]
    for c in task.get("api_contracts") or []:
        parts.append(f"{c.get('method', '')} {c.get('path', '')}")
    for v in task.get("validation") or []:
        parts.append(str(v.get("path") or v.get("endpoint") or v.get("command") or ""))
    return "\n".join(parts)


def _file_paths(task: dict) -> list[str]:
    files = task.get("files") or {}
    out = []
    for group in ("create", "modify"):
        for f in files.get(group) or []:
            out.append(str(f.get("path", "") if isinstance(f, dict) else f))
    return out


def classify_task(task: dict) -> dict:
    """-> {"risk": "low"|"medium"|"high", "reasons": [str]}."""
    text = _task_text(task)
    paths = _file_paths(task)
    all_text = text + "\n" + "\n".join(paths)

    high_reasons = [label for pat, label in _HIGH_MARKERS
                    if re.search(pat, all_text, re.IGNORECASE)]
    if high_reasons:
        return {"risk": "high", "reasons": sorted(set(high_reasons))}

    low_reasons = [label for pat, label in _LOW_MARKERS
                   if re.search(pat, text, re.IGNORECASE)]
    # Files-only signal: every declared file is a test or doc.
    if paths and all(_TEST_PATH.search(p) or _DOC_PATH.search(p) for p in paths):
        low_reasons.append("all declared files are tests/docs")
    if low_reasons:
        return {"risk": "low", "reasons": sorted(set(low_reasons))}

    return {"risk": "medium", "reasons": ["no low-risk markers; defaults to medium"]}


_ORDER = {"low": 0, "medium": 1, "high": 2}


def classify_run(tasks: list[dict]) -> dict:
    """Aggregate: the run's risk is its riskiest task."""
    by_task = {str(t.get("id") or t.get("task_id")): classify_task(t) for t in tasks}
    worst = max(by_task.values(), key=lambda c: _ORDER[c["risk"]],
                default={"risk": "medium"})
    return {"risk": worst["risk"], "by_task": by_task}
