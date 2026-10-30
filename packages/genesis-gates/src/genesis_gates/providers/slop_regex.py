"""slop-regex provider — deterministic checks for known AI-slop design patterns.

Port of the landing-site ``slop-lint.mjs`` ban list (the "anti-slop" G-rules):
the visual tells that make AI-generated frontends read as generated — gradient
text, blue→purple→pink palettes, reflexive glassmorphism, particle canvases.
Each rule is a per-line regex; matches become findings so the built app is
checked against the design it was supposed to follow.

Pure stdlib and always available. Two documented judgment-tier rules are NOT
here because a line regex cannot catch them (G8 "Inter as the only typeface"
and accent-frequency limits) — those live in the design brief that generation
prompts carry.

Config (``.genesis/config.yaml`` → ``gates.design-lint``):

    disable: [G3]                     # rule ids to skip (e.g. a family that
                                      # legitimately uses backdrop-blur)
    rules_file: .genesis/design-rules.yaml   # extra project rules:
                                      # rules: [{id, name, pattern, severity?, remediation?}]

``lint_html()`` is exposed module-level so callers (e.g. the Genesis server's
bake-off critique) can lint candidate HTML strings with the same rule table.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..engine.base import Finding, GateContext, GateResult, GateStatus, Severity
from ..engine.provider import Provider, register_provider


@dataclass(frozen=True)
class SlopRule:
    id: str
    name: str
    pattern: re.Pattern
    severity: Severity = Severity.medium
    remediation: str = ""


def _rule(rid: str, name: str, pattern: str, remediation: str,
          flags: int = 0) -> SlopRule:
    return SlopRule(id=rid, name=name, pattern=re.compile(pattern, flags),
                    remediation=remediation)


# The mechanized ban list. Mirrors scripts/slop-lint.mjs in the design-process
# repo; rule ids are stable so projects can disable individual rules.
BUILTIN_RULES: tuple[SlopRule, ...] = (
    _rule("G1", "gradient-clipped text", r"gradient-text|bg-clip-text",
          "Use a solid text color from the design tokens; gradient text is the "
          "most recognizable AI-design tell."),
    _rule("G2", "blue-purple-pink gradient",
          r"from-(?:blue|indigo|violet|purple)-\d+[^\"']*to-(?:pink|fuchsia|purple)-\d+",
          "Pick colors from the project's design tokens instead of the default "
          "LLM gradient palette."),
    _rule("G3", "glassmorphism surface", r"backdrop-blur|\bglass(?:-card|-surface)?\b",
          "Use the design system's surface tokens; translucent blur cards are a "
          "copied default, not a choice."),
    _rule("G4", "infinite glow pulse", r"glow-effect|animate-pulse-glow",
          "Remove looping glow; motion must carry information."),
    _rule("G5", "decorative blur blob", r"blur-3xl",
          "Delete decorative blurred blobs; establish depth with the design "
          "system's spacing and borders."),
    _rule("G6", "particle/nebula canvas", r"NebulaCanvas|NodeFieldCanvas|particle",
          "Remove canvas particle backgrounds.", re.IGNORECASE),
    _rule("G7", "emoji iconography", r"[\U0001F300-\U0001FAFF☀-⛿]",
          "Replace emoji with the design system's icon set."),
    _rule("G9", "eyebrow+gradient heading formula", r"uppercase[^\n]*tracking-wider[^\n]*block",
          "Vary section headers; the uppercase-eyebrow formula on every section "
          "is a template smell."),
    _rule("G10", "three-stop surface gradient",
          r"from-[\w/\[\]#-]+\s+via-[\w/\[\]#-]+\s+to-[\w/\[\]#-]+",
          "Use flat surface colors from the design tokens; three-stop gradients "
          "are cheap depth."),
)

# UI source the rules apply to.
_EXTENSIONS = {".html", ".css", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"}
_SKIP_DIRS = {".git", ".genesis-work", ".genesis", "gss", "node_modules", "dist",
              "build", ".next", "__pycache__", ".venv", "venv", "coverage"}

_SEVERITIES = {s.value: s for s in Severity}


def load_extra_rules(rules_file: Path) -> list[SlopRule]:
    """Load project-specific rules (e.g. locked family guardrails) from YAML.

    Malformed entries are skipped rather than crashing the gate — a broken
    custom rule must not take down the whole scan.
    """
    try:
        doc = yaml.safe_load(rules_file.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return []
    rules: list[SlopRule] = []
    for entry in doc.get("rules") or []:
        if not isinstance(entry, dict):
            continue
        rid, pattern = entry.get("id"), entry.get("pattern")
        if not rid or not pattern:
            continue
        try:
            compiled = re.compile(str(pattern))
        except re.error:
            continue
        rules.append(SlopRule(
            id=str(rid),
            name=str(entry.get("name", rid)),
            pattern=compiled,
            severity=_SEVERITIES.get(str(entry.get("severity", "medium")),
                                     Severity.medium),
            remediation=str(entry.get("remediation", "")),
        ))
    return rules


def _lint_lines(lines: list[str], rules: list[SlopRule],
                location_prefix: str = "") -> list[Finding]:
    findings: list[Finding] = []
    for i, line in enumerate(lines, start=1):
        for rule in rules:
            if rule.pattern.search(line):
                findings.append(Finding(
                    gate="design-lint",
                    title=f"{rule.id} {rule.name}",
                    severity=rule.severity,
                    detail=line.strip()[:160],
                    location=f"{location_prefix}:{i}" if location_prefix else f"line {i}",
                    remediation=rule.remediation or None,
                    raw={"rule": rule.id},
                ))
    return findings


def lint_html(html: str, extra_rules: list[SlopRule] | None = None,
              disable: set[str] | None = None) -> list[Finding]:
    """Lint an HTML/CSS/JS string with the built-in + extra rules.

    Used by callers outside the gate runner (bake-off candidate critique in
    the Genesis server) so both layers share one rule table.
    """
    disable = disable or set()
    rules = [r for r in (*BUILTIN_RULES, *(extra_rules or [])) if r.id not in disable]
    return _lint_lines(html.split("\n"), rules)


def _iter_ui_files(repo: Path):
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _EXTENSIONS:
            continue
        rel = path.relative_to(repo)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        yield path, rel


@register_provider
class SlopRegexProvider(Provider):
    key = "slop-regex"
    ecosystem = "frontend"
    description = "Deterministic anti-slop design lint (G-rule regex ban list)."

    def is_available(self, ctx: GateContext) -> bool:
        return True  # pure stdlib

    def run(self, ctx: GateContext) -> GateResult:
        start = time.perf_counter()
        repo = Path(ctx.repo_path)
        disable = {str(x) for x in (self.settings.get("disable") or [])}

        extra: list[SlopRule] = []
        rules_file = self.settings.get("rules_file")
        if rules_file:
            rf = Path(rules_file)
            extra = load_extra_rules(rf if rf.is_absolute() else repo / rf)

        rules = [r for r in (*BUILTIN_RULES, *extra) if r.id not in disable]

        findings: list[Finding] = []
        scanned = 0
        for path, rel in _iter_ui_files(repo):
            scanned += 1
            try:
                lines = path.read_text(errors="replace").split("\n")
            except OSError:
                continue
            findings.extend(_lint_lines(lines, rules, str(rel)))

        duration = time.perf_counter() - start
        if scanned == 0:
            return GateResult(
                gate=self.key, status=GateStatus.skipped,
                summary="no UI files found to lint",
                duration_s=duration,
            )
        if not findings:
            return GateResult(
                gate=self.key, status=GateStatus.passed,
                summary=f"clean: no slop patterns in {scanned} UI file(s)",
                duration_s=duration,
            )
        by_rule: dict[str, int] = {}
        for f in findings:
            rid = f.raw.get("rule", "?")
            by_rule[rid] = by_rule.get(rid, 0) + 1
        summary = (f"{len(findings)} slop pattern(s) in {scanned} UI file(s): "
                   + ", ".join(f"{k}×{v}" for k, v in sorted(by_rule.items())))
        return GateResult(
            gate=self.key, status=GateStatus.failed,
            summary=summary, findings=findings,
            duration_s=duration,
        )
