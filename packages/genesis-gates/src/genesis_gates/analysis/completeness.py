"""System-completeness analysis — "systems, not features" (shared core).

AI agents translate features to code literally: a live-tested generated app
shipped signup + Keycloak + JWT with **no sign-in**, because no feature named
returning users. Systems imply mandatory flows. This module holds the profile
definitions and the pure gap analysis, decoupled from any input source:

- Genesis pipelines feed it planning artifacts (``genesis_workdir_input``).
- ``genesis adopt`` feeds it a raw-repo scan (``repo_input`` — see
  :mod:`genesis_gates.analysis.repo_scan`).

Matching is two-tier: *markers* (prose-ish text) activate a profile; flow
coverage is judged against the **implementation surface** (routes, contract
paths, implemented-task titles) — never prose, because "Redirect to login page"
is a reference, not an implementation. (The real failing app mentioned "login"
16 times while implementing it zero times.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MandatoryFlow:
    key: str
    title: str
    # Case-insensitive regex; surface_only flows match only implementation
    # surface, others match the full marker text.
    pattern: str
    # Guidance for whoever fixes the gap (LLM task injection, audit report).
    brief: str
    fallback_files: tuple[str, ...] = ()
    fallback_criteria: tuple[str, ...] = ()
    surface_only: bool = False


@dataclass(frozen=True)
class SystemProfile:
    key: str
    markers: tuple[str, ...]
    flows: tuple[MandatoryFlow, ...]
    required_deps: tuple[dict, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CompletenessInput:
    """Source-agnostic input: what the project *mentions* vs what it *implements*."""
    marker_text: str      # prose-ish: architecture docs, dep manifests, configs
    surface_text: str     # implementation surface: routes, paths, task titles

    @staticmethod
    def build(marker_text: str, surface_text: str) -> CompletenessInput:
        return CompletenessInput(marker_text.lower(), surface_text.lower())


AUTH_PROFILE = SystemProfile(
    key="auth",
    markers=("keycloak", "jwt", "oauth", "oidc", "bearerauth", "authentication",
             "next-auth", "passport", "auth0", "clerk", "supabase.auth",
             "django.contrib.auth", "devise"),
    flows=(
        MandatoryFlow(
            key="sign_in",
            title="Implement returning-user sign-in (login)",
            # Surface-only: matches contract/file paths ("/login") or task
            # titles ("Implement user login") — never prose references.
            pattern=r"(log[-_]?in\b|sign[-_]?in\b|/token\b|/session\b)",
            surface_only=True,
            brief=(
                "The app has signup + sessions but NO way for an existing user "
                "to sign in. Implement POST login: validate email+password "
                "against the stored hash, return the SAME token/session shape "
                "signup returns. Return an identical 401 body for unknown email "
                "and wrong password (never reveal which); 403 when the email is "
                "unverified. Wire the existing frontend sign-in form (replace "
                "any 'not available yet' stub), storing the session exactly "
                "like the signup flow does."
            ),
            fallback_files=(
                "backend login endpoint (e.g. POST /api/*/auth/login)",
                "frontend sign-in form wired to the login endpoint",
            ),
            fallback_criteria=(
                "An existing verified user can sign in with email+password and "
                "receives session tokens",
                "Unknown email and wrong password return an identical 401 response",
                "Unverified accounts receive 403 with a clear message",
            ),
        ),
        MandatoryFlow(
            key="password_reset",
            title="Implement password reset flow",
            pattern=r"(password[-_/ ]?reset|reset[-_/ ]?password|forgot[-_/ ]?password)",
            surface_only=True,
            brief=(
                "Implement password reset: a request endpoint (always returns "
                "200 whether or not the email exists; emails a single-use, "
                "expiring token link — when SMTP is unconfigured, log the link "
                "AND return it in a dev-only response field) plus a completion "
                "endpoint, with the frontend forgot/reset forms."
            ),
            fallback_files=(
                "backend password-reset endpoints (request + complete)",
                "frontend forgot-password and reset-password forms",
            ),
            fallback_criteria=(
                "A user can request a reset link and set a new password with a "
                "valid token",
                "Expired or reused tokens are rejected with a clear error",
                "The request endpoint responds identically whether or not the "
                "email exists",
            ),
        ),
        MandatoryFlow(
            key="admin_seed",
            title="Seed a pre-verified admin account on first boot",
            pattern=r"(admin.{0,24}(seed|account|bootstrap)|seed.{0,24}admin|ADMIN_EMAIL)",
            brief=(
                "Add a startup seed (migration or app-startup hook) that "
                "creates a pre-verified admin account from ADMIN_EMAIL and "
                "ADMIN_PASSWORD env vars, idempotently. If unset, log a clear "
                "warning and skip in dev; document both vars in .env.example."
            ),
            fallback_files=(
                "backend seed script/migration creating the admin from "
                "ADMIN_EMAIL/ADMIN_PASSWORD",
            ),
            fallback_criteria=(
                "On first boot with ADMIN_EMAIL/ADMIN_PASSWORD set, a "
                "pre-verified admin account exists",
                "Re-running the seed is a no-op when the admin already exists",
            ),
        ),
    ),
    required_deps=(
        {
            "env_var": "ADMIN_EMAIL", "type": "credential", "service": "Application",
            "required": True, "default_strategy": "user_must_provide",
            "default_value": None,
            "description": "Email for the seeded admin account "
                           "(created pre-verified on first boot).",
            "doc_url": None,
        },
        {
            "env_var": "ADMIN_PASSWORD", "type": "credential", "service": "Application",
            "required": True, "default_strategy": "auto_generate_strong_password",
            "default_value": None,
            "description": "Password for the seeded admin account.",
            "doc_url": None,
        },
    ),
)

PAYMENTS_PROFILE = SystemProfile(
    key="payments",
    markers=("stripe", "braintree", "paypal", "checkout.session", "payment_intent",
             "lemonsqueezy"),
    flows=(
        MandatoryFlow(
            key="webhook_verification",
            title="Verify payment-provider webhook signatures",
            # Surface-only: a webhook route/endpoint must exist; prose like
            # "Stripe sends webhooks" is a reference, not an implementation.
            pattern=r"(webhook)",
            surface_only=True,
            brief=(
                "Payment state changes (charge succeeded, dispute, refund) "
                "arrive via provider webhooks. Implement a webhook endpoint "
                "that verifies the provider's signature (e.g. Stripe's "
                "Stripe-Signature header) on the RAW request body before "
                "parsing, rejects unsigned/invalid events with 400, and never "
                "trusts unverified event payloads to mutate order or "
                "subscription state."
            ),
            fallback_files=(
                "backend webhook endpoint (e.g. POST /api/*/payments/webhook) "
                "with provider signature verification",
            ),
            fallback_criteria=(
                "Webhook events with a valid provider signature update payment "
                "state",
                "Events with a missing or invalid signature are rejected with "
                "400 and change nothing",
                "Replayed events (same event id) are processed at most once",
            ),
        ),
        MandatoryFlow(
            key="refund_flow",
            title="Implement a refund flow",
            pattern=r"(refund)",
            surface_only=True,
            brief=(
                "The app takes payments but has no way to give money back. "
                "Implement a refund endpoint (admin- or owner-authorized) that "
                "calls the provider's refund API, records the refund against "
                "the original charge, and reflects refunded state in the UI. "
                "Support partial refunds if the provider does."
            ),
            fallback_files=(
                "backend refund endpoint calling the provider's refund API",
            ),
            fallback_criteria=(
                "An authorized user can refund a completed charge",
                "The refund is recorded against the original payment and "
                "visible in the app",
            ),
        ),
        MandatoryFlow(
            key="idempotency",
            title="Use idempotency keys on charge creation",
            pattern=r"(idempotency[-_ ]?key)",
            brief=(
                "Network retries against a payment API can double-charge. Send "
                "an idempotency key (e.g. Stripe's Idempotency-Key header) on "
                "every charge/payment-intent creation, derived from your own "
                "order id, so a retried request can never create a second "
                "charge."
            ),
            fallback_files=(
                "payment-creation calls passing an idempotency key derived "
                "from the order id",
            ),
            fallback_criteria=(
                "Retrying a charge-creation request with the same order "
                "produces exactly one charge",
            ),
        ),
    ),
    # Intentionally empty for v1: required env vars (webhook secrets, API keys)
    # vary by provider; wrong guesses are worse than none.
    required_deps=(),
)

FILE_UPLOAD_PROFILE = SystemProfile(
    key="file_upload",
    markers=("multipart/form-data", "python-multipart", "multer", "busboy",
             "uploadfile", "createreadstream"),
    flows=(
        MandatoryFlow(
            key="size_limit",
            title="Enforce an upload size limit",
            pattern=r"(max[-_ ]?file[-_ ]?size|max[-_ ]?upload|content[-_ ]?length"
                    r"|\b\d+\s?mb\b)",
            brief=(
                "Unbounded uploads are a denial-of-service vector. Enforce a "
                "maximum upload size server-side (reject with 413 before "
                "buffering the whole file), make the limit configurable, and "
                "state it in the UI and API docs."
            ),
            fallback_files=(
                "server-side upload size limit (e.g. max content-length check "
                "returning 413)",
            ),
            fallback_criteria=(
                "Uploads over the configured size limit are rejected with 413 "
                "before being stored",
            ),
        ),
        MandatoryFlow(
            key="type_validation",
            title="Validate uploaded file types",
            pattern=r"(allowed[-_ ]?(file[-_ ]?)?types|mime[-_ ]?type"
                    r"|content[-_ ]?type.{0,20}valid|file[-_ ]?extension"
                    r"|file[-_ ]?(type|format)|unsupported.{0,16}(file|format|media))",
            brief=(
                "Accepting arbitrary file types enables stored-XSS (HTML/SVG) "
                "and malware distribution. Validate uploads against an "
                "allowlist of expected types — check the content (magic "
                "bytes), not just the extension or client-sent Content-Type — "
                "and reject everything else with a clear 415 error."
            ),
            fallback_files=(
                "server-side upload type validation against an allowlist",
            ),
            fallback_criteria=(
                "Files outside the allowed types are rejected with 415",
                "Validation checks file content, not only the extension or "
                "client-sent Content-Type",
            ),
        ),
        MandatoryFlow(
            key="delete_flow",
            title="Let users delete uploaded files",
            # Surface-only: a DELETE route (or delete endpoint/page) must
            # exist; repo_scan includes HTTP methods in the surface text.
            pattern=r"(delete)",
            surface_only=True,
            brief=(
                "Users can upload files but never remove them — a storage-cost "
                "leak and a privacy/GDPR problem. Implement a delete endpoint "
                "(owner-authorized) that removes both the stored object and "
                "its database record, plus the corresponding UI affordance."
            ),
            fallback_files=(
                "backend DELETE endpoint removing the stored file and its record",
                "frontend delete affordance on uploaded files",
            ),
            fallback_criteria=(
                "The file's owner can delete an uploaded file",
                "Deleting removes both the stored object and its metadata "
                "record",
            ),
        ),
    ),
)

PROFILES: tuple[SystemProfile, ...] = (AUTH_PROFILE, PAYMENTS_PROFILE,
                                       FILE_UPLOAD_PROFILE)


def detect_profiles(inp: CompletenessInput) -> list[SystemProfile]:
    """Which system profiles apply, judged from the marker text."""
    return [p for p in PROFILES if any(m in inp.marker_text for m in p.markers)]


def flow_gaps(profile: SystemProfile, inp: CompletenessInput) -> list[MandatoryFlow]:
    """Flows with no evidence in the implementation surface (or markers, for
    non-surface flows)."""
    gaps = []
    for f in profile.flows:
        haystack = inp.surface_text if f.surface_only else (
            inp.marker_text + "\n" + inp.surface_text)
        if not re.search(f.pattern, haystack, re.IGNORECASE):
            gaps.append(f)
    return gaps


def analyze(inp: CompletenessInput) -> dict:
    """Full completeness report: {profiles: [...], gaps: [{profile, flow, title,
    brief}...]}."""
    out: dict = {"profiles": [], "gaps": []}
    for profile in detect_profiles(inp):
        out["profiles"].append(profile.key)
        for f in flow_gaps(profile, inp):
            out["gaps"].append({
                "profile": profile.key, "flow": f.key,
                "title": f.title, "brief": f.brief,
            })
    return out
