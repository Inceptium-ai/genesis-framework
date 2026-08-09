"""Report renderers: terminal (plain ANSI-light) and self-contained HTML."""

from __future__ import annotations

import html

_BAR_W = 24


def _bar(points: float, maximum: float) -> str:
    filled = int(round(_BAR_W * (points / maximum))) if maximum else 0
    return "█" * filled + "░" * (_BAR_W - filled)


def _baseline_lines(cmp: dict) -> list[str]:
    """The CHANGES SINCE BASELINE block (report has a `baseline_comparison`)."""
    delta = cmp["score_delta"]
    arrow = "▲" if delta > 0 else "▼" if delta < 0 else "="
    head = f"  score {arrow} {delta:+.1f}"
    if cmp["grade_change"]:
        head += f"   grade {cmp['grade_change']}"
    lines = [
        "CHANGES SINCE BASELINE" + ("  — REGRESSION" if cmp["regressed"] else ""),
        head,
        f"  findings: {len(cmp['new_findings'])} new, "
        f"{len(cmp['resolved_findings'])} resolved   ·   "
        f"gaps: {len(cmp['new_gaps'])} new, "
        f"{len(cmp['resolved_gaps'])} resolved",
    ]
    for f in cmp["new_findings"][:5]:
        loc = f" ({f['location']})" if f.get("location") else ""
        lines.append(f"  + [{f.get('severity')}] {f.get('gate')}: "
                     f"{f.get('title')}{loc}")
    for g in cmp["new_gaps"][:5]:
        lines.append(f"  + gap: {g.get('title')}")
    for f in cmp["resolved_findings"][:5]:
        lines.append(f"  − resolved: {f.get('title')}")
    for g in cmp["resolved_gaps"][:5]:
        lines.append(f"  − closed gap: {g.get('title')}")
    lines.append("")
    return lines


def render_terminal(report: dict) -> str:
    r = report["readiness"]
    lines = [
        "",
        f"Genesis Agent-Readiness Report — {report['repo']}",
        f"Generated {report['generated_at']}",
        "=" * 72,
        "",
        f"  READINESS SCORE: {r['score']}/100   GRADE: {r['grade']}",
        "",
    ]
    for cat in r["categories"]:
        lines.append(f"  {cat['key']:<14} {_bar(cat['points'], cat['max'])} "
                     f"{cat['points']}/{cat['max']}")
    lines.append("")
    if cmp := report.get("baseline_comparison"):
        lines += _baseline_lines(cmp)
    for cat in r["categories"]:
        lines.append(f"── {cat['key']} " + "─" * (68 - len(cat["key"])))
        lines += [f"   {d}" for d in cat["details"][:10]]
        if len(cat["details"]) > 10:
            lines.append(f"   … {len(cat['details']) - 10} more (see HTML/JSON report)")
        lines.append("")
    gaps = report["completeness"].get("gaps", [])
    if gaps:
        lines.append("MANDATORY-FLOW GAPS (highest-priority remediation):")
        for g in gaps:
            lines.append(f"  • {g['title']}")
        lines.append("")

    review = report.get("ai_review")
    if review and "error" not in review:
        lines.append("AI SECOND-READER (additive — does not alter the score):")
        if review.get("executive_summary"):
            lines.append(f"  {review['executive_summary']}")
            lines.append("")
        for f in review.get("additional_findings", []):
            lines.append(f"  + [{f['severity']}/{f['area']}] {f['title']}")
        for a in review.get("annotations", [])[:6]:
            lines.append(f"  ~ {a['ref']}: {a['note']}")
        if review.get("remediation_plan"):
            lines.append("  Remediation order:")
            for i, step in enumerate(review["remediation_plan"], 1):
                lines.append(f"    {i}. {step}")
        lines.append("")
    return "\n".join(lines)


def _baseline_html(cmp: dict) -> str:
    """CHANGES SINCE BASELINE section — mirrors `_baseline_lines`."""
    e = html.escape
    delta = cmp["score_delta"]
    arrow = "▲" if delta > 0 else "▼" if delta < 0 else "="
    cls = "up" if delta > 0 else "down" if delta < 0 else ""
    badge = ("<span class='chip chip-bad'>REGRESSION</span>"
             if cmp["regressed"] else "")
    grade = (f" · grade {e(cmp['grade_change'])}" if cmp["grade_change"] else "")
    rows: list[str] = []
    for f in cmp["new_findings"]:
        loc = f" <small>({e(f['location'])})</small>" if f.get("location") else ""
        rows.append(f"<li class='base-new'>+ [{e(str(f.get('severity')))}] "
                    f"{e(str(f.get('gate')))}: {e(str(f.get('title')))}{loc}</li>")
    rows += [f"<li class='base-new'>+ gap: {e(str(g.get('title')))}</li>"
             for g in cmp["new_gaps"]]
    rows += [f"<li class='base-resolved'>− resolved: {e(str(f.get('title')))}</li>"
             for f in cmp["resolved_findings"]]
    rows += [f"<li class='base-resolved'>− closed gap: {e(str(g.get('title')))}</li>"
             for g in cmp["resolved_gaps"]]
    return (
        f"<section><h2>Changes since baseline {badge}</h2>"
        f"<p class='delta {cls}'>score {arrow} {delta:+.1f}{grade} · "
        f"{len(cmp['new_findings'])} new / {len(cmp['resolved_findings'])} "
        f"resolved findings · {len(cmp['new_gaps'])} new / "
        f"{len(cmp['resolved_gaps'])} closed gaps</p>"
        + (f"<ul>{''.join(rows)}</ul>" if rows else "")
        + "</section>"
    )


def _history_html(history: list[dict] | None, max_shown: int = 12) -> str:
    """Score-trend strip: one bar per audit, most recent last/highlighted."""
    if not history or len(history) < 2:
        return ""
    e = html.escape
    shown = history[-max_shown:]
    bars = "".join(
        f"<div class='trend-bar{' cur' if i == len(shown) - 1 else ''}' "
        f"title='{e(str(h.get('generated_at', '')))} — "
        f"{h.get('score')}/100 ({e(str(h.get('grade', '')))})'>"
        f"<div style='height:{max(4, round(float(h.get('score', 0))))}%'></div>"
        f"<span>{h.get('score')}</span></div>"
        for i, h in enumerate(shown)
    )
    prefix = "… " if len(history) > len(shown) else ""
    return (
        f"<section><h2>Score history <span>{prefix}{len(history)} runs</span></h2>"
        f"<div class='trend'>{bars}</div></section>"
    )


def _review_html(report: dict) -> str:
    review = report.get("ai_review")
    if not review or "error" in review:
        return ""
    e = html.escape
    findings = "".join(
        f"<li><strong>[{e(f['severity'])}/{e(f['area'])}]</strong> "
        f"{e(f['title'])}<br><small>{e(f['detail'])}</small></li>"
        for f in review.get("additional_findings", [])
    )
    notes = "".join(
        f"<li><em>{e(a['ref'])}</em> — {e(a['note'])}</li>"
        for a in review.get("annotations", [])
    )
    plan = "".join(f"<li>{e(s)}</li>" for s in review.get("remediation_plan", []))
    return (
        "<section><h2>AI second-reader "
        "<span style='font-size:12px;color:#9aa7b8'>additive-only — does not "
        "alter the deterministic score</span></h2>"
        f"<p>{e(review.get('executive_summary', ''))}</p>"
        + (f"<h3>Additional findings</h3><ul>{findings}</ul>" if findings else "")
        + (f"<h3>Annotations</h3><ul>{notes}</ul>" if notes else "")
        + (f"<h3>Remediation order</h3><ol>{plan}</ol>" if plan else "")
        + "</section>"
    )


def render_html(report: dict, history: list[dict] | None = None) -> str:
    r = report["readiness"]
    e = html.escape

    def cat_block(cat: dict) -> str:
        pct = 100 * cat["points"] / cat["max"] if cat["max"] else 0
        def _cls(d: str) -> str:
            return "ok" if d.startswith("✓") else "bad" if d.startswith("✗") else ""

        rows = "".join(
            f'<li class="{_cls(d)}">{e(d)}</li>' for d in cat["details"]
        )
        return (
            f"<section><h2>{e(cat['key'])} <span>{cat['points']}/{cat['max']}</span></h2>"
            f"<div class='meter'><div style='width:{pct:.0f}%'></div></div>"
            f"<ul>{rows}</ul></section>"
        )

    gaps = report["completeness"].get("gaps", [])
    # Gaps are mandatory-flow misses — styled like high-severity findings
    # (they render as `error` in the SARIF output).
    gaps_html = "".join(
        f"<li class='gap'><span class='sev sev-high'>required flow</span> "
        f"<strong>{e(g['title'])}</strong>"
        f"<br><small>{e(g['brief'])}</small></li>"
        for g in gaps
    )
    gate_rows = "".join(
        f"<tr><td>{e(str(g.get('gate')))}</td><td class='s-{e(str(g.get('status')))}'>"
        f"{e(str(g.get('status')))}</td><td>{e(str(g.get('summary', ''))[:120])}</td></tr>"
        for g in report["gates"]
    )
    grade_hue = {"A": 130, "B": 90, "C": 48, "D": 28, "F": 4}.get(r["grade"], 0)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Genesis Readiness — {e(report['repo'])}</title>
<style>
 body{{font:15px/1.5 -apple-system,Segoe UI,sans-serif;margin:0;background:#0e1116;color:#dde3ea}}
 main{{max-width:880px;margin:0 auto;padding:40px 24px}}
 h1{{font-size:22px}} h2{{font-size:16px;display:flex;justify-content:space-between}}
 .score{{display:flex;align-items:center;gap:20px;margin:24px 0}}
 .badge{{font-size:44px;font-weight:700;padding:18px 26px;border-radius:14px;
        background:hsl({grade_hue} 65% 18%);color:hsl({grade_hue} 80% 65%)}}
 .meter{{height:8px;background:#1c2330;border-radius:4px;overflow:hidden}}
 .meter div{{height:100%;background:#3fb68b}}
 section{{margin:26px 0;padding:18px;background:#151a22;border-radius:10px}}
 ul{{margin:10px 0 0;padding-left:20px}} li.ok{{color:#7fd6ac}} li.bad{{color:#e08a8a}}
 table{{width:100%;border-collapse:collapse;font-size:14px}}
 td{{padding:6px 8px;border-top:1px solid #232b38}}
 .s-passed{{color:#7fd6ac}} .s-failed{{color:#e08a8a}} .s-skipped{{color:#9aa7b8}}
 small{{color:#9aa7b8}}
 .chip{{font-size:11px;font-weight:600;padding:2px 8px;border-radius:9px;vertical-align:middle}}
 .chip-bad{{background:#3a1d1d;color:#e08a8a}}
 .delta{{margin:4px 0 10px;color:#9aa7b8}} .delta.up{{color:#7fd6ac}} .delta.down{{color:#e08a8a}}
 .base-new{{color:#e08a8a}} .base-resolved{{color:#7fd6ac}}
 .sev{{font-size:11px;font-weight:600;padding:1px 7px;border-radius:9px;margin-right:6px}}
 .sev-high{{background:#3a1d1d;color:#e08a8a}}
 li.gap{{margin:8px 0;list-style:none;border-left:3px solid #a04545;padding-left:10px}}
 section ul:has(li.gap){{padding-left:4px}}
 .trend{{display:flex;gap:5px;align-items:flex-end;height:84px;margin-top:12px}}
 .trend-bar{{display:flex;flex-direction:column;justify-content:flex-end;align-items:center;
   height:64px;width:26px}}
 .trend-bar div{{width:100%;background:#28564a;border-radius:3px 3px 0 0}}
 .trend-bar.cur div{{background:#3fb68b}}
 .trend-bar span{{font-size:10px;color:#9aa7b8;margin-top:3px}}
 a{{color:#7fb3d6}}
</style></head><body><main>
<h1>Genesis Agent-Readiness Report</h1>
<p><code>{e(report['repo'])}</code> · {e(report['generated_at'])}</p>
<div class="score"><div class="badge">{e(r['grade'])}</div>
<div><strong style="font-size:28px">{r['score']}/100</strong><br>
<small>agent-readiness score</small></div></div>
{_baseline_html(report['baseline_comparison']) if report.get('baseline_comparison') else ''}
{_history_html(history)}
{''.join(cat_block(c) for c in r['categories'])}
{f"<section><h2>Mandatory-flow gaps</h2><ul>{gaps_html}</ul></section>" if gaps else ""}
{_review_html(report)}
<section><h2>Gate results</h2><table>{gate_rows}</table></section>
<p><small>Generated by <code>genesis adopt</code> — part of the
<a href="https://genesis.dev">Genesis</a> delivery framework
(schema: <code>readiness-report.schema.json</code>).</small></p>
</main></body></html>"""
