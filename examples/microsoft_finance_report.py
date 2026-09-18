"""Self-contained product renderer for Microsoft Finance Intelligence."""
from __future__ import annotations

import html
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


COLORS = ("#4cc9f0", "#8ce99a", "#ffd166", "#b197fc", "#ff8787", "#74c0fc")


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _label(value: str) -> str:
    aliases = {
        "cfo": "Operating cash flow",
        "fcf": "Free cash flow",
        "rpo": "Remaining performance obligations",
        "icfr": "Internal control over financial reporting",
        "eps": "Diluted EPS",
        "ppe": "Property & equipment",
        "sbc": "Stock compensation",
        "usd_b": "($B)",
        "yoy": "year over year",
    }
    words = value.replace("_", " ").split()
    rendered = " ".join(aliases.get(word.lower(), word) for word in words)
    return rendered[:1].upper() + rendered[1:]


def _fmt(key: str, value: Any, *, compact: bool = False) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(_e(item) for item in value) if value else "None"
    if isinstance(value, str):
        return _e(value)
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return "N/A"
        if key.endswith("_pp") or "change_pp" in key:
            return f"{value:+.1f} pp"
        if key.endswith("_usd_b") or "market_cap" in key or "enterprise_value_usd_b" in key:
            return f"${value:,.1f}B"
        if key in {"market_price", "price", "base_fair_value_per_share", "fair_value_per_share", "fifty_day_moving_average", "two_hundred_day_moving_average"}:
            return f"${value:,.2f}"
        if "eps" in key and "growth" not in key:
            return f"${value:,.2f}"
        if key.endswith("_m"):
            return f"{value:,.1f}M"
        percent_terms = (
            "growth", "margin", "yield", "rate", "return", "intensity", "conversion",
            "volatility", "drawdown", "upside", "distance", "correlation", "mix", "to_assets",
            "pct_revenue", "accrual_ratio", "diluted_share_change", "reverse_dcf", "cagr",
            "leverage_spread", "quality",
        )
        if any(term in key for term in percent_terms) and "rank" not in key:
            return f"{value * 100:+.1f}%" if value < 0 else f"{value * 100:.1f}%"
        if any(term in key for term in ("multiple", "coverage", "current_ratio", "to_operating_income", "price_to", "enterprise_value_to", "beta")):
            return f"{value:,.2f}x"
        if compact and abs(value) >= 1000:
            return f"{value / 1000:,.1f}K"
        return f"{value:,.2f}"
    return _e(value)


def _metric_tiles(metrics: Mapping[str, Any], limit: int = 8) -> str:
    tiles = []
    for key, value in list(metrics.items())[:limit]:
        tiles.append(
            f'<div class="metric"><span>{_e(_label(key))}</span><strong>{_fmt(key, value)}</strong></div>'
        )
    return "".join(tiles)


def _finding_card(finding: Mapping[str, Any]) -> str:
    severity = str(finding.get("severity", "neutral")).lower()
    refs = " ".join(
        f'<a href="#{_id(str(ref))}" title="Open source record">{_e(ref)}</a>'
        for ref in finding.get("evidence_refs", [])
    )
    facts = " ".join(
        f'<a href="#{_id(str(fact_id))}" title="Open selected fact lineage">{_e(fact_id)}</a>'
        for fact_id in finding.get("fact_ids", [])
    )
    return f"""
    <article class="finding finding-{_id(severity)}">
      <div class="finding-head"><span class="severity">{_e(severity.upper())}</span><span class="kind">{_e(finding.get('kind', 'calculated'))}</span></div>
      <h4>{_e(finding.get('headline', 'Finding'))}</h4>
      <p>{_e(finding.get('detail', ''))}</p>
    <div class="refs">{refs}{facts}</div>
    </article>"""


def _specialists(analyses: Sequence[Mapping[str, Any]]) -> str:
    cards = []
    for item in analyses:
        output = item["output"]
        severities = [str(finding.get("severity", "neutral")).lower() for finding in output.get("findings", [])]
        tags = " ".join(sorted(set(severities)))
        search_text = " ".join([
            str(output.get("title", "")),
            str(output.get("verdict", "")),
            str(output.get("summary", "")),
            tags,
            *(
                f"{finding.get('headline', '')} {finding.get('detail', '')}"
                for finding in output.get("findings", [])
            ),
        ]).lower()
        refs = " ".join(f'<a href="#{_id(ref)}">{_e(ref)}</a>' for ref in output.get("evidence_refs", []))
        findings = "".join(_finding_card(finding) for finding in output.get("findings", []))
        caveats = "".join(f"<li>{_e(item)}</li>" for item in output.get("caveats", []))
        questions = "".join(f"<li>{_e(question)}</li>" for question in output.get("diligence_questions", []))
        assumptions = "".join(f"<li>{_e(value)}</li>" for value in output.get("assumptions", []))
        assumption_panel = f"<h5>Model assumptions</h5><ul>{assumptions}</ul>" if assumptions else ""
        cards.append(f"""
        <article class="specialist" data-search="{_e(search_text)}" data-severity="{_e(tags)}">
          <div class="specialist-top">
            <div><span class="overline">ATTENUATED SPECIALIST · {_e(output['component'].replace('_', '.').upper())}</span><h3>{_e(output['title'])}</h3></div>
            <div class="confidence"><b>{float(output['confidence']):.0%}</b><span>uncalibrated analytical confidence · not probability</span></div>
          </div>
          <div class="verdict-line"><span>{_e(output['verdict'])}</span></div>
          <p class="specialist-summary">{_e(output['summary'])}</p>
          <div class="confidence-bar"><i style="width:{float(output['confidence']):.0%}"></i></div>
          <div class="metric-grid">{_metric_tiles(output.get('metrics', {}), 12)}</div>
          <div class="finding-grid">{findings}</div>
          <details><summary>Professional review checklist</summary><div class="review-grid"><div><h5>Caveats</h5><ul>{caveats}</ul>{assumption_panel}</div><div><h5>Diligence questions</h5><ol>{questions}</ol></div></div></details>
          <div class="evidence-line"><span>SIGNED WHY-RECORD <code>{_e(item['evidence_id'])}</code></span><span>SOURCES {refs}</span></div>
        </article>""")
    return "".join(cards)


def _line_chart(series: Mapping[str, Mapping[str, float]], *, title: str, height: int = 300) -> str:
    years = sorted({year for values in series.values() for year in values})
    if not years:
        return ""
    values = [float(value) for data in series.values() for value in data.values() if value is not None]
    high = max(values) if values else 1
    low = min(0.0, min(values) if values else 0)
    span = high - low or 1
    width = 860
    left, right, top, bottom = 58, 20, 32, 48
    plot_width = width - left - right
    plot_height = height - top - bottom
    grid = []
    for step in range(5):
        y = top + step * plot_height / 4
        value = high - step * span / 4
        grid.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" class="gridline"/><text x="{left-9}" y="{y+4:.1f}" text-anchor="end">{value:,.0f}</text>')
    labels = []
    for index, year in enumerate(years):
        x = left + index * plot_width / max(1, len(years) - 1)
        labels.append(f'<text x="{x:.1f}" y="{height-15}" text-anchor="middle">FY{_e(year)}</text>')
    lines = []
    legend = []
    for index, (name, data) in enumerate(series.items()):
        color = COLORS[index % len(COLORS)]
        points = []
        circles = []
        for year_index, year in enumerate(years):
            if year not in data:
                continue
            x = left + year_index * plot_width / max(1, len(years) - 1)
            y = top + (high - float(data[year])) / span * plot_height
            points.append(f"{x:.1f},{y:.1f}")
            circles.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{color}"><title>{_e(name)} FY{_e(year)}: ${float(data[year]):,.1f}B</title></circle>')
        if points:
            lines.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>{"".join(circles)}')
            legend.append(f'<span><i style="background:{color}"></i>{_e(name)}</span>')
    return f"""<figure class="viz"><figcaption><b>{_e(title)}</b><span>USD billions · SEC XBRL</span></figcaption><div class="legend">{''.join(legend)}</div><svg viewBox="0 0 {width} {height}" role="img" aria-label="{_e(title)}">{''.join(grid)}{''.join(labels)}{''.join(lines)}</svg></figure>"""


def _quarterly_chart(
    series: Mapping[str, Mapping[str, float]], *, title: str, height: int = 310
) -> str:
    periods = sorted(
        {period for values in series.values() for period in values},
        key=lambda value: (int(value[2:6]), int(value[-1])),
    )
    if not periods:
        return ""
    values = [float(value) for data in series.values() for value in data.values() if value is not None]
    high, low = max(values), min(0.0, min(values))
    span = high - low or 1
    width, left, right, top, bottom = 980, 62, 20, 32, 55
    plot_width, plot_height = width - left - right, height - top - bottom
    grid = []
    for step in range(5):
        y = top + step * plot_height / 4
        value = high - step * span / 4
        grid.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" class="gridline"/><text x="{left-9}" y="{y+4:.1f}" text-anchor="end">{value:,.0f}</text>')
    labels = []
    for index, period in enumerate(periods):
        x = left + index * plot_width / max(1, len(periods) - 1)
        labels.append(f'<text x="{x:.1f}" y="{height-16}" text-anchor="middle">{_e(period.replace("FY", ""))}</text>')
    lines, legend = [], []
    for index, (name, data) in enumerate(series.items()):
        color = COLORS[index % len(COLORS)]
        points, circles = [], []
        for period_index, period in enumerate(periods):
            if period not in data:
                continue
            value = float(data[period])
            x = left + period_index * plot_width / max(1, len(periods) - 1)
            y = top + (high - value) / span * plot_height
            points.append(f"{x:.1f},{y:.1f}")
            circles.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{color}"><title>{_e(name)} {_e(period)}: ${value:,.1f}B</title></circle>')
        if points:
            lines.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="3.3" stroke-linecap="round" stroke-linejoin="round"/>{"".join(circles)}')
            legend.append(f'<span><i style="background:{color}"></i>{_e(name)}</span>')
    return f'<figure class="viz"><figcaption><b>{_e(title)}</b><span>Fiscal quarters · USD billions · SEC XBRL</span></figcaption><div class="legend">{"".join(legend)}</div><svg viewBox="0 0 {width} {height}" role="img" aria-label="{_e(title)}">{"".join(grid)}{"".join(labels)}{"".join(lines)}</svg></figure>'


def _quality_matrix(quality: Mapping[str, Any]) -> str:
    rows = []
    for item in quality.get("dimensions", []):
        score = max(0.0, min(1.0, float(item.get("score", 0))))
        rows.append(f"""
        <div class="quality-row"><div><b>{_e(item.get('dimension'))}</b><span>{_e(item.get('basis'))}</span></div><div class="quality-score"><b>{score:.0%}</b><span>{_e(item.get('status'))}</span></div><div class="quality-track"><i style="width:{score:.1%}"></i></div></div>""")
    return "".join(rows)


def _review_panel(review: Mapping[str, Any], integrity: Mapping[str, Any]) -> str:
    reviewers = review.get("required_reviewers", [])
    decisions = {
        vote.get("reviewer"): str(vote.get("decision", "pending")).upper()
        for vote in review.get("votes", [])
    }
    reviewer_rows = "".join(
        f'<li><b>{_e(reviewer)}</b><span>{_e(decisions.get(reviewer, "PENDING"))}</span></li>'
        for reviewer in reviewers
    )
    comments = "".join(
        f'<li><b>{_e(item.get("reviewer"))}</b><span>{_e(item.get("comment"))}</span></li>'
        for item in review.get("comments", [])
    ) or '<li><span>No review comments recorded.</span></li>'
    authorized = bool(review.get("release_authorized"))
    return f"""
    <div class="review-hero {'review-approved' if authorized else 'review-pending'}">
      <span class="overline">DIGEST-BOUND RELEASE CONTROL</span><h3>{_e(str(review.get('status', 'unknown')).upper())}</h3>
      <p>Review <code>{_e(review.get('id'))}</code> is bound to <code>{_e(str(review.get('artifact_digest', '')))}</code>.</p>
    <p>Digest match: <b>{'VERIFIED' if review.get('artifact_digest_matches') else 'FAILED'}</b> · Approval valid for digest: <b>{'YES' if review.get('approval_valid_for_digest') else 'NO'}</b></p>
      <div class="review-verdict">{'RELEASE AUTHORIZED FOR THIS EXACT DIGEST' if authorized else 'EXTERNAL / CONSEQUENTIAL RELEASE NOT AUTHORIZED'}</div>
    </div>
    <div class="grid-2 review-detail"><div><h4>Named reviewers</h4><ul class="reviewers">{reviewer_rows}</ul></div><div><h4>Comments</h4><ul class="reviewers">{comments}</ul></div></div>
    <p class="rights-note">Review event chain: {'VERIFIED' if integrity.get('verified') else 'FAILED'} · Identity boundary: {_e(review.get('identity_assurance', 'authenticated identity integration required'))}</p>"""


def _fact_lineage(facts: Mapping[str, Mapping[str, Any]]) -> str:
    cards = []
    ordered = sorted(
        facts.items(), key=lambda item: (str(item[1].get("period")), item[0])
    )
    for fact_id, fact in ordered:
        inputs = fact.get("inputs", [])
        input_links = " ".join(
            f'<a href="#{_id(str(item.get("fact_id")))}">{_e(item.get("fact_id"))}</a>'
            for item in inputs
            if item.get("fact_id")
        )
        recasts = fact.get("recast_chain", [])
        recast_rows = "".join(
            f'<li><code>{_e(item.get("accession"))}</code> · {_e(item.get("filed"))} · {_e(item.get("value"))}{" · SELECTED" if item.get("selected") else ""}</li>'
            for item in recasts
        )
        source_id = str(fact.get("source_id", ""))
        search_value = " ".join(str(value) for value in (
            fact_id,
            fact.get("metric"),
            fact.get("entity"),
            fact.get("member"),
            fact.get("concept"),
            fact.get("period"),
            source_id,
        )).lower()
        cards.append(f"""
        <details class="fact-card" id="{_id(fact_id)}" data-fact="{_e(search_value)}">
          <summary><span><b>{_e(fact.get('metric'))}</b> · {_e(fact.get('period'))}</span><strong>{_e(fact.get('value'))} {_e(fact.get('normalized_unit'))}</strong></summary>
          <div class="fact-body"><p><code>{_e(fact_id)}</code> · {_e(fact.get('namespace'))}:{_e(fact.get('concept'))}</p>
          <dl><dt>Source</dt><dd><a href="#{_id(source_id)}">{_e(source_id)}</a></dd><dt>Entity / member</dt><dd>{_e(fact.get('entity') or fact.get('member') or 'Consolidated')}</dd><dt>Filed</dt><dd>{_e(fact.get('filed'))}</dd><dt>Accession</dt><dd>{_e(fact.get('accession'))}</dd><dt>Method</dt><dd>{_e(fact.get('selection_method'))}</dd><dt>Formula</dt><dd>{_e(fact.get('formula'))}</dd></dl>
          {f'<p>Input facts: {input_links}</p>' if input_links else ''}{f'<ul>{recast_rows}</ul>' if recast_rows else ''}</div>
        </details>""")
    return "".join(cards)


def _sparkline(observations: Sequence[Mapping[str, Any]], width: int = 700, height: int = 210) -> str:
    values = [float(item["close"]) for item in observations if item.get("close") is not None]
    if not values:
        return ""
    if len(values) > 190:
        step = max(1, len(values) // 190)
        values = values[::step] + ([values[-1]] if values[-1] != values[::step][-1] else [])
    low, high = min(values), max(values)
    span = high - low or 1
    points = []
    for index, value in enumerate(values):
        x = 8 + index * (width - 16) / max(1, len(values) - 1)
        y = height - 16 - (value - low) / span * (height - 34)
        points.append(f"{x:.1f},{y:.1f}")
    area = f"8,{height-16} " + " ".join(points) + f" {width-8},{height-16}"
    return f"""<svg class="spark" viewBox="0 0 {width} {height}" role="img" aria-label="One-year indicative Microsoft adjusted price history"><defs><linearGradient id="price-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#4cc9f0" stop-opacity=".38"/><stop offset="1" stop-color="#4cc9f0" stop-opacity="0"/></linearGradient></defs><polygon points="{area}" fill="url(#price-fill)"/><polyline points="{' '.join(points)}" fill="none" stroke="#4cc9f0" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/></svg>"""


def _segment_bars(rows: Sequence[Mapping[str, Any]]) -> str:
    maximum = max((float(item.get("revenue_usd_b") or 0) for item in rows), default=1)
    output = []
    for item in rows:
        value = float(item.get("revenue_usd_b") or 0)
        output.append(f"""
        <div class="bar-row"><div class="bar-title"><b>{_e(item.get('segment'))}</b><span>{_fmt('revenue_usd_b', value)} · {_fmt('revenue_growth', item.get('revenue_growth'))} growth · {_fmt('operating_margin', item.get('operating_margin'))} margin</span></div><div class="hbar"><i style="width:{value/maximum*100:.1f}%"></i></div></div>""")
    return "".join(output)


def _table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str], *, table_class: str = "") -> str:
    if not rows:
        return '<div class="empty">No normalized rows available.</div>'
    head = "".join(f"<th>{_e(_label(column))}</th>" for column in columns)
    body = []
    for row in rows:
        cells = "".join(f"<td>{_fmt(column, row.get(column))}</td>" for column in columns)
        body.append(f"<tr>{cells}</tr>")
    return f'<div class="table-scroll"><table class="{_e(table_class)}"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _sources(sources: Mapping[str, Mapping[str, Any]]) -> str:
    rows = []
    for source_id, item in sources.items():
        authoritative = bool(item.get("authoritative"))
        evidence_class = str(item.get("evidence_class") or "")
        if evidence_class.startswith("regulatory"):
            badge = "REGULATORY"
        elif evidence_class.startswith("filed annual"):
            badge = "FILED / AUDITED"
        elif evidence_class.startswith("filed interim"):
            badge = "FILED / INTERIM"
        elif evidence_class.startswith("company-reported"):
            badge = "COMPANY-REPORTED"
        elif evidence_class.startswith("official macro"):
            badge = "OFFICIAL MACRO"
        else:
            badge = "INDICATIVE"
        authority_class = "authoritative" if authoritative else "indicative"
        rows.append(f"""
        <tr id="{_id(source_id)}" data-source="{_e((source_id + ' ' + str(item.get('provider')) + ' ' + str(item.get('description'))).lower())}">
          <td><b>{_e(source_id)}</b><br><span class="source-badge {authority_class}">{badge}</span></td>
          <td><b>{_e(item.get('provider'))}</b><small>{_e(item.get('description'))}</small></td>
          <td>{_e(item.get('retrieved_at'))}<small>{_e(item.get('retrieval_mode'))} · {_e(item.get('freshness'))}</small></td>
          <td><code title="{_e(item.get('sha256'))}">{_e(str(item.get('sha256', ''))[:16])}…</code><small>{int(item.get('bytes', 0)):,} bytes</small></td>
          <td><a class="source-link" href="{_e(item.get('url'))}" target="_blank" rel="noreferrer">Open source ↗</a></td>
        </tr>""")
    return "".join(rows)


def _markdown_value(key: str, value: Any) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(_fmt(key, value)))


def _budget_percent(value: Any) -> float:
    try:
        used, limit = str(value).split("/", 1)
        return max(0.0, min(100.0, float(used) / float(limit) * 100))
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def render_microsoft_markdown(package: Mapping[str, Any], destination: Path) -> Path:
    """Write a detailed, portable analyst report alongside the product dashboard."""
    product = package["product"]
    synthesis = package["synthesis"]
    sec = package["source_data"]["sec"]
    lines = [
        f"# {product['name']} — Microsoft ({synthesis['ticker']})",
        "",
        f"**Edition:** {product['edition']}  ",
        f"**Generated:** {product['generated_at']}  ",
        f"**Latest filing:** {sec['filing']['fiscal_year']} Form 10-K, filed {sec['filing']['filingDate']}  ",
        f"**Decision posture:** {synthesis['decision_posture']}  ",
        f"**Review status:** {product['classification']}",
        "",
        "> Controlled pilot only. This is not investment, audit, tax, legal, credit, or regulatory advice.",
        "",
        "## Executive synthesis",
        "",
        synthesis["summary"],
        "",
        f"**Recommendation:** {synthesis['recommendation']}  ",
        f"**Uncalibrated analytical confidence:** {synthesis['confidence']:.0%} (not a probability)  ",
        f"**Source objects:** {synthesis['source_coverage']}",
        "",
    ]
    for thesis_name in ("bull", "base", "bear"):
        lines += [f"### {thesis_name.title()} case", ""]
        lines += [f"- {item}" for item in synthesis["thesis"][thesis_name]]
        lines.append("")
    lines += ["## Decision triggers", "", "| Signal | Constructive evidence | Thesis-break evidence |", "|---|---|---|"]
    for trigger in synthesis["decision_triggers"]:
        lines.append(f"| {trigger['signal']} | {trigger['green']} | {trigger['red']} |")
    lines.append("")

    quarterly = next(
        item["output"] for item in package["analyses"]
        if item["output"]["component"] == "quarterly_change_intelligence"
    )
    filing_changes = sec.get("filing_changes", {})
    lines += [
        "## What changed: quarterly and filing intelligence", "",
        quarterly["summary"], "",
        "### Latest quarterly and TTM metrics", "",
        "| Metric | Value |", "|---|---:|",
    ]
    for key, value in quarterly["metrics"].items():
        lines.append(f"| {_label(key)} | {_markdown_value(key, value)} |")
    lines += ["", "### Annual disclosure-change triage", ""]
    if filing_changes.get("changes"):
        lines += ["| Category | Topic | Change | Prior | Current | Basis |", "|---|---|---|---|---|---|"]
        for change in filing_changes["changes"]:
            lines.append(
                f"| {change['category']} | {change['topic']} | {change['change']} | "
                f"{change.get('prior_value')} | {change.get('current_value')} | {change.get('basis')} |"
            )
    else:
        lines.append("No deterministic topic-level change was detected; professional redline review remains required.")
    lines += ["", "### Recent filing timeline", "", "| Filed | Form | Report date | Items | Accession |", "|---|---|---|---|---|"]
    for filing_item in sec.get("filing_timeline", []):
        lines.append(
            f"| {filing_item.get('filingDate')} | {filing_item.get('form')} | "
            f"{filing_item.get('reportDate')} | {filing_item.get('items') or ''} | "
            f"{filing_item.get('accessionNumber')} |"
        )
    lines.append("")

    lines += ["## Specialist analyses", ""]
    for item in package["analyses"]:
        output = item["output"]
        lines += [
            f"### {output['title']}", "",
            f"**Verdict:** {output['verdict']}  ",
            f"**Uncalibrated analytical confidence:** {output['confidence']:.0%} (not a probability)  ",
            f"**Signed evidence:** `{item['evidence_id']}`  ",
            f"**Source references:** {', '.join(output['evidence_refs'])}", "",
            output["summary"], "", "#### Metrics", "",
            "| Metric | Value |", "|---|---:|",
        ]
        for key, value in output.get("metrics", {}).items():
            lines.append(f"| {_label(key)} | {_markdown_value(key, value)} |")
        lines += ["", "#### Findings", ""]
        for finding in output.get("findings", []):
            facts = f"; selected facts: {', '.join(finding.get('fact_ids', []))}" if finding.get("fact_ids") else ""
            lines.append(f"- **[{finding['severity'].upper()}] {finding['headline']}** — {finding['detail']} ({', '.join(finding['evidence_refs'])}{facts})")
        if output.get("tables"):
            for table_name, rows in output["tables"].items():
                lines += ["", f"#### {_label(table_name)}", ""]
                if rows:
                    columns = list(rows[0])
                    lines.append("| " + " | ".join(_label(column) for column in columns) + " |")
                    lines.append("|" + "|".join("---" for _ in columns) + "|")
                    for row in rows:
                        lines.append("| " + " | ".join(_markdown_value(column, row.get(column)) for column in columns) + " |")
        lines += ["", "#### Caveats", ""] + [f"- {value}" for value in output.get("caveats", [])]
        if output.get("assumptions"):
            lines += ["", "#### Assumptions", ""] + [f"- {value}" for value in output["assumptions"]]
        lines += ["", "#### Professional diligence questions", ""] + [f"1. {value}" for value in output.get("diligence_questions", [])]
        lines += ["", "---", ""]

    lines += ["## Source and evidence manifest", "", "| Source ID | Provider | Evidence class | Retrieved | SHA-256 | URL |", "|---|---|---|---|---|---|"]
    for source_id, source in package["sources"].items():
        lines.append(
            f"| {source_id} | {source['provider']} | {source.get('evidence_class', 'Unclassified')} | "
            f"{source['retrieved_at']} | `{source['sha256']}` | {source['url']} |"
        )
    lines += ["", "## Governance proof", ""]
    for proof in package["governance"]["guarantees"]:
        lines.append(f"- **{'PASS' if proof['holds'] else 'FAIL'} — {proof['invariant']}**: {proof['reason']}")
    integrity = package["governance"]["integrity"]
    review = package["governance"].get("review", {})
    evidence_quality = package["governance"].get("evidence_quality", {})
    package_validation = package["governance"].get("package_validation", {})
    lines += [
        f"- **Signed ledger:** {'verified' if integrity['verified'] else 'failed'}; {integrity['record_count']} records; broken record: {integrity['broken_record']}",
        f"- **Shared budget:** {package['governance']['budget']}",
        f"- **Release digest:** `{package['governance'].get('release', {}).get('content_digest')}`",
        f"- **Named review:** {review.get('status', 'unknown')} — `{review.get('id')}` — release authorized: {review.get('release_authorized', False)}",
        f"- **Package acceptance gate:** {'passed' if package_validation.get('passed') else 'failed'} — {package_validation.get('check_count', 0)} deterministic checks",
        "",
        "## Evidence quality dimensions", "",
        "> Scores are deterministic quality/completeness indicators, not calibrated probabilities.", "",
        "| Dimension | Score | Status | Basis |", "|---|---:|---|---|",
    ]
    for dimension in evidence_quality.get("dimensions", []):
        lines.append(
            f"| {dimension['dimension']} | {dimension['score']:.0%} | "
            f"{dimension['status']} | {dimension['basis']} |"
        )
    lines += [
        "", "## Selected fact lineage", "",
        "| Fact ID | Metric | Period | Concept | Value | Filed | Accession | Derivation |",
        "|---|---|---|---|---:|---|---|---|",
    ]
    for fact_id, fact in sorted(
        sec.get("fact_index", {}).items(),
        key=lambda item: (str(item[1].get("period")), item[0]),
    ):
        lines.append(
            f"| `{fact_id}` | {fact.get('metric')} | {fact.get('period')} | "
            f"{fact.get('concept')} | {fact.get('value')} {fact.get('normalized_unit', '')} | "
            f"{fact.get('filed') or ''} | {fact.get('accession') or ''} | "
            f"{fact.get('formula') or fact.get('selection_method')} |"
        )
    lines += [
        "",
        "## Methodology and limitations", "",
    ]
    for key, value in package["methodology"].items():
        if isinstance(value, list):
            lines += [f"### {_label(key)}", ""] + [f"- {item}" for item in value] + [""]
        else:
            lines += [f"- **{_label(key)}:** {value}"]
    lines += ["", "## Required professional review", "", "The dashboard and report are decision-support artifacts. Accountable professionals must validate source completeness, accounting classifications, model assumptions, legal interpretations, tax outcomes, market-data rights, and fitness for the intended decision before reliance.", ""]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination


def render_microsoft_report(package: Mapping[str, Any], destination: Path) -> Path:
    """Render the buyer-ready, interactive, responsive HTML product."""
    product = package["product"]
    synthesis = package["synthesis"]
    data = package["source_data"]
    sec = data["sec"]
    filing = sec["filing"]
    governance = package["governance"]
    by_name = {item["output"]["component"]: item["output"] for item in package["analyses"]}
    performance = by_name["financial_performance"]
    cash_flow = by_name["cash_flow_capital_intensity"]
    valuation = by_name["valuation_scenarios"]
    market = by_name["market_risk"]
    ai = by_name["ai_cloud_strategy"]
    quarterly = by_name["quarterly_change_intelligence"]
    filing_change = by_name["filing_change_detection"]
    segment_rows = by_name["segment_economics"]["tables"]["segments"]
    product_rows = by_name["segment_economics"]["tables"]["products"]
    peer_rows = by_name["peer_benchmarking"]["tables"]["peer_benchmark"]
    scenario_rows = valuation["tables"]["dcf_scenarios"]
    risk_rows = by_name["regulatory_operational_risk"]["tables"]["risk_register"]
    price_observations = data["market"]["microsoft"]["observations"]
    quarterly_rows = quarterly["tables"]["quarterly_history"]
    ttm_rows = quarterly["tables"]["ttm_history"]
    filing_change_rows = filing_change["tables"]["filing_changes"]
    filing_timeline_rows = filing_change["tables"]["filing_timeline"]
    review = governance.get("review", {})
    evidence_quality = governance.get("evidence_quality", {})
    package_validation = governance.get("package_validation", {})

    financial_chart = _line_chart(
        {
            "Revenue": sec["annual"]["revenue"],
            "Operating income": sec["annual"]["operating_income"],
            "Operating cash flow": sec["annual"]["operating_cash_flow"],
            "Capex": sec["annual"]["capex"],
        },
        title="Five-year scale, earnings, cash flow, and reinvestment",
    )
    quarterly_chart = _quarterly_chart(
        {
            "Revenue": sec["quarterly"]["revenue"],
            "Operating income": sec["quarterly"]["operating_income"],
            "Operating cash flow": sec["quarterly"]["operating_cash_flow"],
            "Capex": sec["quarterly"]["capex"],
            "Free cash flow": sec["quarterly"]["free_cash_flow"],
        },
        title="Quarterly momentum and cash conversion",
    )
    ttm_chart = _quarterly_chart(
        {
            "TTM revenue": sec["ttm_series"].get("revenue", {}),
            "TTM operating cash flow": sec["ttm_series"].get("operating_cash_flow", {}),
            "TTM capex": sec["ttm_series"].get("capex", {}),
            "TTM free cash flow": sec["ttm_series"].get("free_cash_flow", {}),
        },
        title="Rolling trailing-twelve-month scale and reinvestment",
    )
    historical_rows = performance["tables"]["historical_financials"]
    cash_rows = cash_flow["tables"]["cash_flow_history"]
    trigger_rows = "".join(
        f"<tr><td><b>{_e(item['signal'])}</b></td><td class=\"green-cell\">{_e(item['green'])}</td><td class=\"red-cell\">{_e(item['red'])}</td></tr>"
        for item in synthesis["decision_triggers"]
    )
    thesis_panels = "".join(
        f'<article class="case case-{name}"><span>{name.upper()} CASE</span><ul>{"".join(f"<li>{_e(point)}</li>" for point in points)}</ul></article>'
        for name, points in synthesis["thesis"].items()
    )
    product_table = _table(product_rows, ("offering", "revenue_usd_b", "growth", "revenue_mix"))
    peer_table = _table(peer_rows, ("ticker", "company", "fiscal_year_end", "revenue_growth", "operating_margin", "free_cash_flow_margin", "capex_intensity"))
    scenario_table = _table(scenario_rows, ("case", "fcf_growth", "discount_rate", "terminal_growth", "fair_value_per_share", "upside_downside"), table_class="scenario-table")
    quarterly_table = _table(
        quarterly_rows,
        ("period", "revenue_usd_b", "revenue_yoy", "operating_margin", "operating_cash_flow_usd_b", "capex_usd_b", "free_cash_flow_usd_b"),
    )
    ttm_table = _table(
        ttm_rows,
        ("period", "revenue_usd_b", "operating_income_usd_b", "operating_cash_flow_usd_b", "capex_usd_b", "free_cash_flow_usd_b"),
    )
    filing_change_table = _table(
        filing_change_rows,
        ("category", "topic", "change", "severity", "prior_value", "current_value", "basis"),
    )
    filing_timeline_table = _table(
        filing_timeline_rows,
        ("filingDate", "form", "reportDate", "items", "accessionNumber", "material_event"),
    )
    quality_matrix = _quality_matrix(evidence_quality)
    release_review = _review_panel(review, governance.get("review_integrity", {}))
    fact_lineage = _fact_lineage(sec.get("fact_index", {}))
    risk_cards = "".join(
        f"""<article class="risk-card" data-score="{item['score']}"><div><span class="risk-score">{item['score']}/9</span><span class="risk-level">{_e(item['impact'])} impact · {_e(item['likelihood'])} likelihood</span></div><h4>{_e(item['risk'])}</h4><p>{_e(item['rationale'])}</p><small>{'10-K TOPIC DETECTED' if item['filing_topic_detected'] else 'ANALYST EXTENSION'} · {_e(item['kind'])}</small></article>"""
        for item in risk_rows
    )
    persona_cards = "".join(
        f"""<article class="persona"><span class="overline">PROFESSIONAL VIEW</span><h3>{_e(report['title'])}</h3><p>{_e(report['decision_use'])}</p><div class="focus-tags">{''.join(f'<span>{_e(_label(value))}</span>' for value in report['focus'])}</div><div class="review-state">{_e(report['review_status'])}</div><small>{_e(report['guardrail'])}</small></article>"""
        for report in package["reports"].values()
    )
    source_rows = _sources(package["sources"])
    proofs = "".join(
        f'<div class="proof"><i>{"✓" if item["holds"] else "!"}</i><div><b>{_e(item["invariant"])}</b><span>{_e(item["reason"])}</span></div></div>'
        for item in governance["guarantees"]
    )
    validation_proof = (
        f'<div class="proof"><i>{"✓" if package_validation.get("passed") else "!"}</i><div><b>Package acceptance gate</b><span>{int(package_validation.get("check_count", 0))} deterministic contract checks passed</span></div></div>'
    )
    chain_rows = "".join(
        f'<tr><td>{index:02d}</td><td>{_e(item["output"]["title"])}</td><td><code>{_e(item["evidence_id"])}</code></td><td><span class="source-badge authoritative">VERIFIED</span></td></tr>'
        for index, item in enumerate(package["analyses"], 1)
    )
    quality_controls = "".join(f"<span>{_e(value)}</span>" for value in package["methodology"]["quality_controls"])
    limitations = "".join(f"<li>{_e(value)}</li>" for value in package["methodology"]["limitations"])
    report_json = json.dumps(package, separators=(",", ":")).replace("</", "<\\/")
    current_price = valuation["metrics"]["market_price"]
    source_cutoff = package["methodology"]["data_cutoff"]
    budget = governance["budget"]
    call_budget_percent = _budget_percent(budget.get("calls"))
    risk_budget_percent = _budget_percent(budget.get("risk"))
    cost_budget_percent = _budget_percent(budget.get("cost"))
    integrity = governance["integrity"]
    latest_revenue = performance["metrics"]["revenue_usd_b"]
    base_scenario = next(item for item in scenario_rows if item["case"] == "Base")
    normalized_fcf = valuation["metrics"]["normalized_fcf_usd_b"]
    shares = valuation["metrics"]["shares_outstanding_b"]
    net_cash = (
        by_name["balance_sheet_credit"]["metrics"]["net_cash_usd_b"]
    )

    document = f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Source-backed governed Microsoft finance intelligence controlled pilot">
<meta name="color-scheme" content="dark light">
<title>Microsoft Finance Intelligence | Autarch</title>
<style>
:root{{--bg:#061018;--bg2:#091722;--panel:#0c1b27;--panel2:#102431;--ink:#edf7ff;--muted:#91a8b9;--line:#244050;--cyan:#4cc9f0;--green:#8ce99a;--gold:#ffd166;--red:#ff8787;--violet:#b197fc;--shadow:0 22px 70px rgba(0,0,0,.27);--radius:20px}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth;scroll-padding-top:82px}}html,body{{max-width:100%;overflow-x:hidden}}body{{margin:0;background:radial-gradient(circle at 85% 0,#113d50 0,transparent 25%),radial-gradient(circle at 3% 28%,#19284c 0,transparent 22%),var(--bg);color:var(--ink);font-family:"Segoe UI Variable","Segoe UI",Arial,sans-serif;line-height:1.56}}a{{color:inherit}}button,input{{font:inherit}}.wrap{{width:min(1260px,calc(100% - 42px));margin:auto}}.overline,.eyebrow{{font-size:11px;letter-spacing:.15em;font-weight:800;color:var(--cyan)}}.muted{{color:var(--muted)}}
.skip{{position:absolute;left:-999px;top:10px;background:#fff;color:#000;padding:8px;z-index:100}}.skip:focus{{left:10px}}header{{position:sticky;top:0;z-index:30;background:rgba(6,16,24,.87);backdrop-filter:blur(18px);border-bottom:1px solid rgba(255,255,255,.08)}}nav{{height:72px;display:flex;align-items:center;justify-content:space-between;gap:22px}}.brand{{font-weight:900;letter-spacing:.09em;font-size:18px;white-space:nowrap}}.brand b{{color:var(--cyan)}}.navlinks{{display:flex;align-items:center;gap:21px;font-size:12px;color:#b3c4d1}}.navlinks a{{text-decoration:none}}.navlinks a:hover{{color:var(--cyan)}}.nav-actions{{display:flex;gap:8px}}.button{{border:1px solid var(--line);background:#102532;color:var(--ink);padding:9px 13px;border-radius:10px;text-decoration:none;cursor:pointer;font-size:12px}}.button:hover{{border-color:var(--cyan)}}
.hero{{padding:75px 0 42px}}.hero-grid{{display:grid;grid-template-columns:1.2fr .8fr;gap:34px;align-items:stretch}}h1{{font-size:clamp(44px,6.7vw,86px);letter-spacing:-.052em;line-height:.96;margin:17px 0 23px;max-width:850px}}h1 em{{font-style:normal;background:linear-gradient(95deg,var(--cyan),#c2f0ff 58%,var(--gold));-webkit-background-clip:text;color:transparent}}.lede{{font-size:18px;color:#b9cbd8;max-width:800px}}.chips,.quality-chips{{display:flex;flex-wrap:wrap;gap:8px;margin-top:24px}}.chip,.quality-chips span{{font-size:10px;font-weight:750;letter-spacing:.08em;border:1px solid var(--line);background:rgba(255,255,255,.03);padding:8px 11px;border-radius:999px}}.chip.live{{color:var(--green);border-color:#376b5e}}.decision{{background:linear-gradient(145deg,#112d3b,#0b1822);border:1px solid #31566b;border-radius:26px;padding:29px;box-shadow:var(--shadow);display:flex;flex-direction:column;justify-content:space-between;overflow:hidden;position:relative}}.decision:after{{content:"";position:absolute;width:200px;height:200px;background:var(--cyan);filter:blur(100px);right:-90px;top:-80px;opacity:.16}}.decision h2{{font-size:26px;line-height:1.16;color:var(--gold);margin:10px 0}}.decision p{{color:#b8cad7}}.decision-meta{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:16px}}.decision-meta div{{background:#081722;padding:12px;border-radius:12px}}.decision-meta span{{display:block;color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.1em}}.decision-meta b{{font-size:16px}}
.kpi-grid{{display:grid;grid-template-columns:repeat(6,1fr);gap:11px;margin:24px auto 62px}}.kpi{{background:rgba(12,27,39,.9);border:1px solid var(--line);border-radius:16px;padding:17px}}.kpi b{{font-size:24px;display:block;line-height:1.2}}.kpi span{{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.09em}}section{{padding:68px 0}}.band{{background:#08151f;border-block:1px solid var(--line)}}.section-head{{display:flex;justify-content:space-between;align-items:end;gap:35px;margin-bottom:28px}}.section-head h2{{font-size:clamp(30px,4vw,46px);letter-spacing:-.034em;margin:6px 0;line-height:1.1}}.section-head p{{max-width:590px;color:var(--muted);margin:0}}.section-number{{font-size:12px;color:var(--gold);font-family:Consolas,monospace}}
.case-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}}.case{{border:1px solid var(--line);background:var(--panel);padding:22px;border-radius:18px}}.case>span{{font-size:10px;font-weight:850;letter-spacing:.11em}}.case ul{{padding-left:18px;margin-bottom:0;color:#bfd0dc;font-size:13px}}.case-bull>span{{color:var(--green)}}.case-base>span{{color:var(--gold)}}.case-bear>span{{color:var(--red)}}.trigger-table{{margin-top:19px}}.green-cell{{border-left:3px solid #3e806e}}.red-cell{{border-left:3px solid #92505b}}
.grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}.grid-2>*,.lab>*,.proof-grid>*,.method-grid>*{{min-width:0}}.panel{{border:1px solid var(--line);background:linear-gradient(155deg,var(--panel2),var(--panel));border-radius:var(--radius);padding:24px;box-shadow:0 10px 30px rgba(0,0,0,.1);min-width:0}}.panel h3{{font-size:22px;margin:5px 0 13px}}.panel>p{{color:#b9cbd7}}.viz{{margin:0;background:#07141d;border:1px solid #1b3544;border-radius:18px;padding:18px;overflow:hidden}}.viz figcaption{{display:flex;justify-content:space-between;gap:15px}}.viz figcaption span{{color:var(--muted);font-size:11px}}.viz svg{{width:100%;height:auto;display:block}}.viz text{{fill:#829aab;font-size:11px}}.gridline{{stroke:#1e3543;stroke-width:1}}.legend{{display:flex;gap:15px;flex-wrap:wrap;margin:9px 0}}.legend span{{font-size:10px;color:#a9bdca}}.legend i{{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}}.table-scroll{{overflow:auto;max-width:100%;border:1px solid var(--line);border-radius:15px}}table{{width:100%;border-collapse:collapse;background:#0a1822}}th,td{{padding:12px 14px;border-bottom:1px solid #1d3544;text-align:left;font-size:12px;white-space:nowrap}}th{{font-size:9px;letter-spacing:.08em;color:var(--muted);text-transform:uppercase;background:#0f202c;position:sticky;top:0}}tbody tr:hover{{background:#102532}}td small{{display:block;color:var(--muted);white-space:normal;margin-top:4px}}.metric-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}}.metric{{background:#081722;border:1px solid #1b3442;padding:11px;border-radius:11px;min-width:0}}.metric span{{display:block;color:var(--muted);font-size:8px;text-transform:uppercase;letter-spacing:.07em;white-space:normal}}.metric strong{{font-size:14px;overflow-wrap:anywhere}}.bar-row{{margin:18px 0}}.bar-title{{display:flex;justify-content:space-between;gap:15px;font-size:12px}}.bar-title span{{color:var(--muted);text-align:right}}.hbar{{height:9px;border-radius:99px;background:#1e3441;margin-top:8px;overflow:hidden}}.hbar i{{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--cyan),var(--violet))}}
.ai-callout{{border-left:3px solid var(--violet);padding:2px 0 2px 16px;margin:18px 0}}.ai-callout b{{display:block;color:var(--violet)}}.price-panel{{position:relative;overflow:hidden}}.price-head{{display:flex;justify-content:space-between;align-items:end}}.price-head strong{{font-size:36px}}.price-head span{{font-size:11px;color:var(--muted)}}.spark{{width:100%;display:block;margin-top:10px}}.rights-note{{font-size:10px;color:var(--muted);border-left:2px solid var(--gold);padding-left:10px}}.lab{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:19px}}.controls{{display:grid;gap:15px}}.control label{{display:flex;justify-content:space-between;font-size:12px}}input[type=range]{{width:100%;accent-color:var(--cyan)}}.lab-result{{display:grid;place-items:center;text-align:center;background:#07141d;border:1px solid #244452;border-radius:17px;min-height:180px}}.lab-result b{{font-size:42px;color:var(--cyan)}}.lab-result span{{font-size:10px;color:var(--muted);display:block;letter-spacing:.1em}}.lab-result small{{color:var(--gold)}}
.toolbar{{display:flex;align-items:center;gap:9px;margin-bottom:18px;flex-wrap:wrap}}.search{{flex:1;min-width:220px;border:1px solid var(--line);background:#0a1923;color:var(--ink);padding:11px 14px;border-radius:11px}}.filter{{border:1px solid var(--line);background:#102532;color:#b9cbd7;padding:9px 12px;border-radius:999px;cursor:pointer;font-size:11px}}.filter.active{{border-color:var(--cyan);color:var(--cyan)}}.specialist-list{{display:grid;gap:18px}}.specialist{{border:1px solid var(--line);background:linear-gradient(145deg,#10232f,#0b1923);border-radius:22px;padding:26px;box-shadow:0 14px 38px rgba(0,0,0,.13)}}.specialist.hidden{{display:none}}.specialist-top{{display:flex;justify-content:space-between;gap:20px}}.specialist h3{{font-size:25px;margin:4px 0}}.confidence{{text-align:right;flex:0 0 auto}}.confidence b{{font-size:25px;color:var(--green);display:block}}.confidence span{{font-size:9px;color:var(--muted);text-transform:uppercase}}.verdict-line span{{display:inline-block;border:1px solid #5f5432;background:#2b2717;color:var(--gold);padding:5px 9px;border-radius:7px;font-size:10px;font-weight:800}}.specialist-summary{{color:#bfd0db;max-width:900px}}.confidence-bar{{height:3px;background:#213947;margin:16px 0 19px;border-radius:10px}}.confidence-bar i{{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--green))}}.finding-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:15px}}.finding{{border:1px solid #203947;background:#081721;border-radius:14px;padding:15px}}.finding h4{{margin:9px 0 6px;font-size:14px}}.finding p{{font-size:12px;color:#aebfcb;margin:0}}.finding-head{{display:flex;justify-content:space-between;gap:8px}}.severity,.kind{{font-size:8px;font-weight:850;letter-spacing:.1em}}.kind{{color:var(--muted);font-weight:500}}.finding-high .severity{{color:var(--red)}}.finding-watch .severity{{color:var(--gold)}}.finding-positive .severity{{color:var(--green)}}.refs{{display:flex;flex-wrap:wrap;gap:5px;margin-top:10px}}.refs a,.evidence-line a{{font-size:8px;text-decoration:none;color:var(--cyan);border:1px solid #21475a;padding:3px 5px;border-radius:5px}}details{{border-top:1px solid var(--line);margin-top:18px;padding-top:14px}}summary{{cursor:pointer;color:var(--cyan);font-size:12px;font-weight:700}}.review-grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;font-size:12px;color:#b4c5d0}}.review-grid h5{{color:var(--ink);font-size:11px;text-transform:uppercase}}.review-grid ul,.review-grid ol{{padding-left:18px}}.evidence-line{{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;color:var(--muted);font-size:9px;border-top:1px solid var(--line);padding-top:13px;margin-top:15px}}code{{font-family:Consolas,monospace;color:var(--cyan);font-size:.92em}}
.risk-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.risk-card{{background:var(--panel);border:1px solid var(--line);border-radius:17px;padding:19px}}.risk-card>div{{display:flex;align-items:center;justify-content:space-between;gap:8px}}.risk-score{{font-weight:900;color:var(--red)}}.risk-level{{font-size:9px;color:var(--muted)}}.risk-card h4{{font-size:15px;margin:14px 0 7px}}.risk-card p{{font-size:11px;color:#afc1cc}}.risk-card small{{font-size:8px;color:var(--gold);letter-spacing:.08em}}.persona-grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}}.persona{{border:1px solid var(--line);background:var(--panel);padding:19px;border-radius:17px}}.persona h3{{font-size:17px;margin:6px 0}}.persona p,.persona small{{font-size:11px;color:#aebfca}}.focus-tags{{display:flex;flex-wrap:wrap;gap:5px;margin:13px 0}}.focus-tags span{{font-size:8px;background:#172c39;padding:5px;border-radius:5px}}.review-state{{font-size:9px;color:var(--gold);border-top:1px solid var(--line);padding-top:10px;margin-top:10px}}
.source-tools{{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:12px}}.source-badge{{display:inline-block;font-size:7px;letter-spacing:.08em;font-weight:850;padding:3px 5px;border-radius:4px;margin-top:4px}}.source-badge.authoritative{{color:var(--green);background:#14372f}}.source-badge.indicative{{color:var(--gold);background:#3b3118}}.source-link{{color:var(--cyan);text-decoration:none}}.source-table td:nth-child(2){{white-space:normal;min-width:260px}}.proof-grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}.proof-panel{{background:var(--panel);border:1px solid var(--line);border-radius:19px;padding:22px}}.proof{{display:flex;gap:11px;margin:13px 0}}.proof i{{display:grid;place-items:center;width:27px;height:27px;border-radius:50%;background:#143b31;color:var(--green);font-style:normal;flex:0 0 auto}}.proof b,.proof span{{display:block;font-size:12px}}.proof span{{color:var(--muted)}}.budget-row{{margin:15px 0}}.budget-row div{{display:flex;justify-content:space-between;font-size:11px}}.budget-track{{height:7px;background:#203642;border-radius:99px;overflow:hidden;margin-top:6px}}.budget-track i{{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--gold))}}.method-grid{{display:grid;grid-template-columns:.8fr 1.2fr;gap:18px}}.method-step{{display:flex;gap:12px;margin:13px 0}}.method-step b{{display:grid;place-items:center;width:28px;height:28px;background:#163243;border-radius:8px;color:var(--cyan);flex:0 0 auto}}.method-step span{{font-size:12px;color:#b5c5d0}}.downloads{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:18px}}.download{{text-decoration:none;border:1px solid var(--line);background:#102532;padding:14px;border-radius:12px}}.download b,.download span{{display:block}}.download span{{font-size:9px;color:var(--muted)}}footer{{padding:42px 0;border-top:1px solid var(--line);color:var(--muted);font-size:11px}}.footer-grid{{display:flex;justify-content:space-between;gap:30px}}.empty{{padding:20px;color:var(--muted)}}
.quality-row{{display:grid;grid-template-columns:1fr auto;gap:6px 18px;border-bottom:1px solid var(--line);padding:13px 0}}.quality-row>div:first-child b,.quality-row>div:first-child span{{display:block}}.quality-row>div:first-child span{{font-size:10px;color:var(--muted)}}.quality-score{{text-align:right}}.quality-score b,.quality-score span{{display:block}}.quality-score span{{font-size:9px;color:var(--muted);text-transform:uppercase}}.quality-track{{grid-column:1/-1;height:5px;background:#203642;border-radius:99px;overflow:hidden}}.quality-track i{{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--green))}}.review-hero{{padding:20px;border:1px solid var(--line);border-radius:17px;background:#091923}}.review-approved{{border-color:#376b5e}}.review-pending{{border-color:#705f32}}.review-hero h3{{font-size:28px;margin:7px 0;color:var(--gold)}}.review-approved h3{{color:var(--green)}}.review-hero code{{overflow-wrap:anywhere}}.review-verdict{{font-size:10px;font-weight:850;letter-spacing:.08em;border-top:1px solid var(--line);padding-top:12px}}.review-detail{{margin-top:14px}}.reviewers{{padding:0;list-style:none}}.reviewers li{{display:flex;justify-content:space-between;gap:12px;padding:8px;border-bottom:1px solid var(--line);font-size:11px}}.reviewers span{{color:var(--muted)}}.fact-list{{display:grid;gap:8px;max-height:740px;overflow:auto;padding-right:4px}}.fact-card{{border:1px solid var(--line);background:#081722;border-radius:12px;scroll-margin-top:92px}}.fact-card:target{{outline:2px solid var(--cyan)}}.fact-card summary{{display:flex;justify-content:space-between;gap:15px;padding:12px;cursor:pointer;font-size:11px}}.fact-card summary strong{{color:var(--cyan);text-align:right}}.fact-body{{border-top:1px solid var(--line);padding:13px;color:var(--muted);font-size:11px}}.fact-body dl{{display:grid;grid-template-columns:auto 1fr;gap:6px 12px}}.fact-body dt{{color:var(--ink)}}.fact-body dd{{margin:0;overflow-wrap:anywhere}}.fact-body a{{color:var(--cyan)}}
@media(max-width:1050px){{.kpi-grid{{grid-template-columns:repeat(3,1fr)}}.risk-grid{{grid-template-columns:repeat(2,1fr)}}.persona-grid{{grid-template-columns:repeat(3,1fr)}}.finding-grid{{grid-template-columns:1fr}}.navlinks{{display:none}}}}
@media(max-width:760px){{.wrap{{width:min(100% - 24px,1260px)}}.hero-grid,.grid-2,.lab,.proof-grid,.method-grid{{grid-template-columns:1fr}}.case-grid{{grid-template-columns:1fr}}.metric-grid{{grid-template-columns:repeat(2,1fr)}}.persona-grid{{grid-template-columns:1fr}}.section-head{{display:block}}.decision-meta{{grid-template-columns:1fr}}.review-grid{{grid-template-columns:1fr}}.nav-actions{{display:none}}.brand{{font-size:16px;letter-spacing:.06em}}}}
@media(max-width:480px){{.kpi-grid,.risk-grid,.metric-grid,.downloads{{grid-template-columns:1fr 1fr}}.brand{{font-size:14px;letter-spacing:.04em}}h1{{font-size:42px}}section{{padding:49px 0}}.specialist{{padding:18px}}.specialist-top{{display:block}}.confidence{{text-align:left;margin-top:9px}}.bar-title{{display:block}}.bar-title span{{display:block;text-align:left}}}}
@media print{{:root{{--bg:#fff;--bg2:#fff;--panel:#fff;--panel2:#f6f8fa;--ink:#15212a;--muted:#5d6d78;--line:#d9e1e6}}body{{background:#fff;font-size:10pt}}header,.toolbar,.nav-actions,.lab{{display:none!important}}.hero{{padding-top:20px}}section{{padding:28px 0}}.specialist,.panel,.persona,.risk-card,.case,.proof-panel{{break-inside:avoid;box-shadow:none}}.finding-grid{{grid-template-columns:repeat(3,1fr)}}a{{text-decoration:none}}}}
/* Defensive narrow-screen containment for long fact identities and native range controls. */
.specialist-list>*,.specialist,.finding-grid,.finding,.controls,.control,.fact-card,.fact-body{{min-width:0}}
.control{{overflow:hidden}}input[type=range]{{display:block;max-width:100%;margin-inline:0}}
.fact-body p,.fact-body code,.fact-body a{{overflow-wrap:anywhere;word-break:break-word}}
.refs{{min-width:0}}.refs a{{max-width:100%;overflow-wrap:anywhere;word-break:break-all}}
.source-tools>*{{min-width:0}}.source-tools .muted{{overflow-wrap:anywhere}}
@media(max-width:760px){{.source-tools{{align-items:stretch;flex-direction:column}}.source-tools .muted{{text-align:left}}}}
</style>
</head>
<body>
<a class="skip" href="#main">Skip to report</a>
<header><div class="wrap"><nav><div class="brand"><b>AUTARCH</b> / FINANCE INTELLIGENCE</div><div class="navlinks"><a href="#cockpit">Cockpit</a><a href="#changes">Changes</a><a href="#financials">Financials</a><a href="#valuation">Valuation</a><a href="#workbench">Workbench</a><a href="#lineage">Lineage</a><a href="#review">Review</a></div><div class="nav-actions"><a class="button" href="microsoft_evidence_bundle.zip" download>Evidence bundle</a><button class="button" type="button" onclick="window.print()">Print / PDF</button></div></nav></div></header>
<main id="main">
<section class="hero"><div class="wrap hero-grid"><div><span class="eyebrow">QUARTERLY & CHANGE INTELLIGENCE · {_e(filing['fiscal_year'])} · CIK {_e(sec['cik'])}</span><h1>Microsoft intelligence with <em>evidence by construction.</em></h1><p class="lede">A governed controlled-pilot product that connects annual and quarterly SEC facts, filing changes, Microsoft disclosures, macro data, market history, and peers to exact selected-fact lineage and persistent named review.</p><div class="chips"><span class="chip live">● {_e(product['freshness'])}</span><span class="chip">{len(package['sources'])} SOURCE OBJECTS</span><span class="chip">15 SPECIALISTS</span><span class="chip">FACT-LEVEL LINEAGE</span><span class="chip">REVIEW {_e(str(review.get('status', 'unknown')).upper())}</span></div></div><aside class="decision"><div><span class="overline">GOVERNED COMMITTEE POSTURE</span><h2>{_e(synthesis['decision_posture'])}</h2><p>{_e(synthesis['summary'])}</p></div><div class="decision-meta"><div><span>Workflow recommendation</span><b>{_e(synthesis['recommendation'])}</b></div><div><span>Uncalibrated analytical confidence · not probability</span><b>{float(synthesis['confidence']):.0%}</b></div><div><span>Latest filing</span><b>{_e(filing['filingDate'])}</b></div><div><span>Release review</span><b>{_e(str(review.get('status', 'unknown')).upper())}</b></div></div></aside></div></section>
<div class="wrap kpi-grid"><div class="kpi"><b>{_fmt('revenue_usd_b',latest_revenue)}</b><span>FY revenue · {_fmt('revenue_growth',performance['metrics']['revenue_growth'])} YoY</span></div><div class="kpi"><b>{_fmt('operating_margin',performance['metrics']['operating_margin'])}</b><span>Operating margin</span></div><div class="kpi"><b>{_fmt('capex_usd_b',cash_flow['metrics']['capex_usd_b'])}</b><span>Capex · {_fmt('capex_intensity',cash_flow['metrics']['capex_intensity'])} of revenue</span></div><div class="kpi"><b>{_fmt('free_cash_flow_usd_b',cash_flow['metrics']['free_cash_flow_usd_b'])}</b><span>CFO less capex</span></div><div class="kpi"><b>{_fmt('market_price',current_price)}</b><span>Indicative market price</span></div><div class="kpi"><b>{_fmt('reverse_dcf_five_year_fcf_growth',valuation['metrics']['reverse_dcf_five_year_fcf_growth'])}</b><span>Reverse-DCF FCF requirement</span></div></div>
<section class="band" id="cockpit"><div class="wrap"><div class="section-head"><div><span class="section-number">01 / EXECUTIVE COCKPIT</span><h2>One evidence base. Three explicit cases.</h2></div><p>Reported facts, deterministic calculations, and analyst inferences remain labeled separately. Decision triggers show what would strengthen or break the thesis.</p></div><div class="case-grid">{thesis_panels}</div><div class="table-scroll trigger-table"><table><thead><tr><th>Signal</th><th>Constructive evidence</th><th>Thesis-break evidence</th></tr></thead><tbody>{trigger_rows}</tbody></table></div></div></section>
<section id="changes"><div class="wrap"><div class="section-head"><div><span class="section-number">02 / WHAT CHANGED</span><h2>Quarterly momentum, TTM economics, and filing deltas</h2></div><p>{_e(quarterly['summary'])} Disclosure comparison is deterministic triage—not a legal redline or materiality conclusion.</p></div>{quarterly_chart}<div style="margin-top:18px">{quarterly_table}</div><div style="margin-top:18px">{ttm_chart}</div><div style="margin-top:18px">{ttm_table}</div><div class="grid-2" style="margin-top:18px"><article class="panel"><span class="overline">DISCLOSURE DELTA</span><h3>{_e(filing_change['verdict'])}</h3>{filing_change_table}</article><article class="panel"><span class="overline">SEC EVENT TIMELINE</span><h3>Recent 10-K, 10-Q, and 8-K sequence</h3>{filing_timeline_table}</article></div></div></section>
<section id="financials"><div class="wrap"><div class="section-head"><div><span class="section-number">03 / FINANCIAL ENGINE</span><h2>Scale, leverage, and the cost of capacity</h2></div><p>The five-year SEC record shows exceptional operating growth and a sharp change in infrastructure intensity. CFO less capex is shown as an analytical—not GAAP—measure.</p></div>{financial_chart}<div class="grid-2" style="margin-top:18px"><div>{_table(historical_rows, ('year','revenue_usd_b','gross_margin','operating_income_usd_b','operating_margin','net_income_usd_b'))}</div><div>{_table(cash_rows, ('year','operating_cash_flow_usd_b','capex_usd_b','free_cash_flow_usd_b','free_cash_flow_margin','capex_intensity'))}</div></div></div></section>
<section class="band" id="business"><div class="wrap"><div class="section-head"><div><span class="section-number">04 / BUSINESS ECONOMICS</span><h2>Cloud-led mix shift, product-level dispersion</h2></div><p>Latest inline-XBRL member facts provide segment and offering views. Management metrics add Azure, Copilot, Microsoft Cloud, and commercial RPO context without implying standalone profitability.</p></div><div class="grid-2"><article class="panel"><span class="overline">REPORTABLE SEGMENTS</span><h3>Revenue, growth, and operating margin</h3>{_segment_bars(segment_rows)}</article><article class="panel"><span class="overline">AI DEMAND vs. AI ECONOMICS</span><h3>{_e(ai['verdict'])}</h3><div class="metric-grid">{_metric_tiles(ai['metrics'],12)}</div><div class="ai-callout"><b>The commercial signal</b><span>Azure, paid Copilot seats, and RPO demonstrate adoption and forward demand.</span></div><div class="ai-callout"><b>The unresolved underwriting question</b><span>Shared infrastructure disclosure does not isolate AI revenue, utilization, fully loaded gross margin, depreciation, or return on deployed capital.</span></div></article></div><div style="margin-top:18px"><span class="overline">OFFERING REVENUE</span><h3>Product and service portfolio</h3>{product_table}</div></div></section>
<section id="valuation"><div class="wrap"><div class="section-head"><div><span class="section-number">05 / VALUATION LAB</span><h2>Make the embedded growth requirement visible</h2></div><p>Scenarios are transparent sensitivities, not price targets. The live sandbox below is deliberately unsigned and does not alter the governed source record.</p></div><div class="grid-2"><article class="panel price-panel"><div class="price-head"><div><span>INDICATIVE ADJUSTED PRICE · {_e(data['market']['microsoft']['as_of'])}</span><strong>{_fmt('market_price',current_price)}</strong></div><div><span>1Y RETURN</span><strong>{_fmt('one_year_return',market['metrics']['one_year_return'])}</strong></div></div>{_sparkline(price_observations)}<p class="rights-note">{_e(data['market']['rights_note'])}</p></article><article class="panel"><span class="overline">REVERSE DCF</span><h3>What the current price requires</h3><div class="metric-grid">{_metric_tiles(valuation['metrics'],12)}</div><p>{_e(valuation['summary'])}</p></article></div><div style="margin-top:18px">{scenario_table}</div><div class="panel lab"><div class="controls"><div class="control"><label><span>Five-year FCF growth</span><b id="growthValue">{base_scenario['fcf_growth']:.1%}</b></label><input id="growth" type="range" min="-5" max="35" step="0.25" value="{base_scenario['fcf_growth']*100:.2f}"></div><div class="control"><label><span>Discount rate</span><b id="discountValue">{base_scenario['discount_rate']:.2%}</b></label><input id="discount" type="range" min="5" max="14" step="0.05" value="{base_scenario['discount_rate']*100:.2f}"></div><div class="control"><label><span>Terminal growth</span><b id="terminalValue">{base_scenario['terminal_growth']:.2%}</b></label><input id="terminal" type="range" min="0" max="5" step="0.05" value="{base_scenario['terminal_growth']*100:.2f}"></div><small class="muted">Starting normalized FCF: {_fmt('normalized_fcf_usd_b',normalized_fcf)} · net cash: {_fmt('net_cash_usd_b',net_cash)} · shares: {_fmt('shares_outstanding_b',shares)}B</small></div><div class="lab-result"><div><span>INTERACTIVE INDICATIVE VALUE</span><b id="labValue">—</b><small id="labDelta">Unsigned sensitivity</small></div></div></div></div></section>
<section class="band" id="peers"><div class="wrap"><div class="section-head"><div><span class="section-number">06 / PEER BENCHMARK</span><h2>Margins lead; cash conversion reflects the build cycle</h2></div><p>Each issuer uses its latest available annual 10-K. The table is an operating benchmark across different fiscal calendars and business mixes—not a valuation comp set.</p></div>{peer_table}</div></section>
<section id="workbench"><div class="wrap"><div class="section-head"><div><span class="section-number">07 / SPECIALIST WORKBENCH</span><h2>Fifteen reviewable analyses</h2></div><p>Every component receives only its declared capability, passes deterministic quality assertions, names caveats and diligence questions, and resolves to signed evidence.</p></div><div class="toolbar"><input class="search" id="analysisSearch" type="search" placeholder="Search specialists, verdicts, and findings…" aria-label="Search specialist analyses"><button class="filter active" data-filter="all">All</button><button class="filter" data-filter="high">High attention</button><button class="filter" data-filter="watch">Watch</button><button class="filter" data-filter="positive">Constructive</button></div><div class="specialist-list" id="specialistList">{_specialists(package['analyses'])}</div></div></section>
<section class="band" id="risk"><div class="wrap"><div class="section-head"><div><span class="section-number">08 / RISK REGISTER</span><h2>Enterprise-scale tails, explicitly separated from fact</h2></div><p>Impact and likelihood are analyst judgments. Filing-topic detection proves disclosure support; it does not assign a probability or forecast an event.</p></div><div class="risk-grid">{risk_cards}</div></div></section>
<section id="personas"><div class="wrap"><div class="section-head"><div><span class="section-number">09 / GO-TO-MARKET DELIVERY</span><h2>One governed record, five professional workflows</h2></div><p>Autarch converts the same traceable evidence into role-specific decision views while preserving source lineage, review state, and professional accountability.</p></div><div class="persona-grid">{persona_cards}</div></div></section>
<section class="band" id="evidence"><div class="wrap"><div class="section-head"><div><span class="section-number">10 / SOURCE LINEAGE</span><h2>Open the source. Verify the hash. Trace the conclusion.</h2></div><p>Raw responses are retained in a content-addressed cache. Each source records provider, URL, retrieval time, authority class, SHA-256, and signed ingestion evidence.</p></div><div class="source-tools"><input class="search" id="sourceSearch" type="search" placeholder="Filter source manifest…" aria-label="Filter source manifest"><span class="muted">{len(package['sources'])} objects · cutoff {_e(source_cutoff)}</span></div><div class="table-scroll"><table class="source-table"><thead><tr><th>Source ID</th><th>Provider / purpose</th><th>Retrieved / mode</th><th>Content hash</th><th>Public URL</th></tr></thead><tbody id="sourceBody">{source_rows}</tbody></table></div></div></section>
<section id="lineage"><div class="wrap"><div class="section-head"><div><span class="section-number">11 / FACT LINEAGE</span><h2>Trace a claim to the selected SEC fact</h2></div><p>Each selected or calculated fact retains concept, unit, period, filing date, accession, selection method, recast chain, formula, and input fact IDs. Finding links jump directly here.</p></div><div class="source-tools"><input class="search" id="factSearch" type="search" placeholder="Filter by fact ID, metric, concept, or period…" aria-label="Filter selected fact lineage"><span class="muted">{len(sec.get('fact_index', {}))} selected and calculated facts</span></div><div class="fact-list" id="factList">{fact_lineage}</div></div></section>
<section class="band" id="review"><div class="wrap"><div class="section-head"><div><span class="section-number">12 / EVIDENCE QUALITY & REVIEW</span><h2>Quality dimensions and accountable release stay separate</h2></div><p>Evidence scores measure inspectable completeness and integrity—not probability. Approval is valid only for the exact canonical release digest and is superseded when content changes.</p></div><div class="grid-2"><article class="panel"><span class="overline">EVIDENCE QUALITY PROFILE · NOT A PROBABILITY</span><h3>Technical quality index {_fmt('quality', evidence_quality.get('technical_quality_index'))}</h3>{quality_matrix}</article><article class="panel">{release_review}</article></div></div></section>
<section id="governance"><div class="wrap"><div class="section-head"><div><span class="section-number">13 / GOVERNANCE PROOF</span><h2>The controls ran—not merely the analysis</h2></div><p>Static invariants, attenuated capabilities, economic budgets, deterministic evaluation, a digest-bound review queue, and tamper-evident action and review chains govern consequences.</p></div><div class="proof-grid"><article class="proof-panel"><h3>Static guarantees & chain integrity</h3>{proofs}<div class="proof"><i>✓</i><div><b>Signed ledger verified</b><span>{integrity['record_count']} records · broken record: {_e(integrity['broken_record'])}</span></div></div>{validation_proof}</article><article class="proof-panel"><h3>Shared economic envelope</h3><div class="budget-row"><div><span>Calls</span><b>{_e(budget['calls'])}</b></div><div class="budget-track"><i style="width:{call_budget_percent:.1f}%"></i></div></div><div class="budget-row"><div><span>Risk units</span><b>{_e(budget['risk'])}</b></div><div class="budget-track"><i style="width:{risk_budget_percent:.1f}%"></i></div></div><div class="budget-row"><div><span>Cost units</span><b>{_e(budget['cost'])}</b></div><div class="budget-track"><i style="width:{cost_budget_percent:.1f}%"></i></div></div><p class="muted">A proposed action that exceeds policy, authority, approval, or budget is refused before its adapter executes.</p></article></div><h3 style="margin-top:24px">Specialist why-record index</h3><div class="table-scroll"><table><thead><tr><th>#</th><th>Specialist</th><th>Signed evidence ID</th><th>Integrity</th></tr></thead><tbody>{chain_rows}</tbody></table></div></div></section>
<section class="band" id="methodology"><div class="wrap"><div class="section-head"><div><span class="section-number">14 / METHODOLOGY & REVIEW</span><h2>Production discipline for public research</h2></div><p>Source authority, freshness, selected-fact lineage, reconciliation, assumptions, and professional sign-off remain visible throughout the workflow.</p></div><div class="method-grid"><article class="panel"><span class="overline">PIPELINE</span><div class="method-step"><b>1</b><span>Discover recent 10-K, 10-Q, and 8-K metadata; retrieve latest and prior annual filings without hard-coded accessions.</span></div><div class="method-step"><b>2</b><span>URL-bind, hash, and cache every response; retain selected facts, recasts, formulas, and input IDs.</span></div><div class="method-step"><b>3</b><span>Build standalone quarters and TTM values, reconcile quarter sums, and run fifteen isolated specialists.</span></div><div class="method-step"><b>4</b><span>Bind exact evidence and analysis content to two named reviewers; changed content supersedes prior approval.</span></div><div class="quality-chips">{quality_controls}</div></article><article class="panel"><span class="overline">LIMITATIONS & PROFESSIONAL STANDARD</span><h3>Decision support, not delegated accountability</h3><ul>{limitations}</ul><p>Accountable reviewers must validate source completeness, classification, estimates, scenario assumptions, legal and tax interpretations, market-data rights, and fitness for the intended decision.</p><div class="downloads"><a class="download" href="microsoft_finance_intelligence.md" download><b>Detailed report</b><span>Markdown</span></a><a class="download" href="microsoft_finance_intelligence.json" download><b>Machine package</b><span>JSON</span></a><a class="download" href="evidence_manifest.json" download><b>Evidence manifest</b><span>JSON</span></a><a class="download" href="artifact_manifest.json" download><b>Artifact hashes</b><span>JSON</span></a><a class="download" href="review_audit.json" download><b>Review audit</b><span>JSON</span></a><a class="download" href="microsoft_evidence_bundle.zip" download><b>Complete bundle</b><span>ZIP</span></a></div></article></div></div></section>
</main>
<footer><div class="wrap footer-grid"><div><b>AUTARCH / FINANCE INTELLIGENCE</b><br>{_e(product['edition'])} · {_e(product['version'])}</div><div>Generated {_e(product['generated_at'])}<br>Controlled pilot only—not investment, audit, tax, legal, credit, or regulatory advice.</div></div></footer>
<script type="application/json" id="autarch-report-data">{report_json}</script>
<script>
(() => {{
  const cards = [...document.querySelectorAll('.specialist')];
  const search = document.getElementById('analysisSearch');
  let activeFilter = 'all';
  function filterCards() {{
    const query = search.value.trim().toLowerCase();
        const tokens = query.split(/\\s+/).filter(Boolean);
    cards.forEach(card => {{
            const textMatch = !tokens.length || tokens.every(token => card.dataset.search.includes(token));
      const severityMatch = activeFilter === 'all' || card.dataset.severity.split(' ').includes(activeFilter);
      card.classList.toggle('hidden', !(textMatch && severityMatch));
    }});
  }}
  search.addEventListener('input', filterCards);
  document.querySelectorAll('.filter').forEach(button => button.addEventListener('click', () => {{
    document.querySelectorAll('.filter').forEach(item => item.classList.remove('active'));
    button.classList.add('active'); activeFilter = button.dataset.filter; filterCards();
  }}));
  const sourceSearch = document.getElementById('sourceSearch');
  sourceSearch.addEventListener('input', () => {{
    const query = sourceSearch.value.trim().toLowerCase();
    document.querySelectorAll('#sourceBody tr').forEach(row => row.hidden = !!query && !row.dataset.source.includes(query));
  }});
    const factSearch = document.getElementById('factSearch');
    factSearch.addEventListener('input', () => {{
        const query = factSearch.value.trim().toLowerCase();
        const tokens = query.split(/\\s+/).filter(Boolean);
        document.querySelectorAll('#factList .fact-card').forEach(card => card.hidden = tokens.some(token => !card.dataset.fact.includes(token)));
    }});
  const growth = document.getElementById('growth');
  const discount = document.getElementById('discount');
  const terminal = document.getElementById('terminal');
  function updateDcf() {{
    const g = Number(growth.value) / 100, r = Number(discount.value) / 100, t = Number(terminal.value) / 100;
    document.getElementById('growthValue').textContent = (g * 100).toFixed(2) + '%';
    document.getElementById('discountValue').textContent = (r * 100).toFixed(2) + '%';
    document.getElementById('terminalValue').textContent = (t * 100).toFixed(2) + '%';
    if (r <= t) {{ document.getElementById('labValue').textContent = 'Invalid'; document.getElementById('labDelta').textContent = 'Discount rate must exceed terminal growth'; return; }}
    let fcf = {float(normalized_fcf):.8f}, pv = 0;
    for (let year = 1; year <= 5; year++) {{ fcf *= (1 + g); pv += fcf / Math.pow(1 + r, year); }}
    const terminalValue = fcf * (1 + t) / (r - t);
    const value = (pv + terminalValue / Math.pow(1 + r, 5) + {float(net_cash):.8f}) / {float(shares):.8f};
    const delta = value / {float(current_price):.8f} - 1;
    document.getElementById('labValue').textContent = '$' + value.toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
    document.getElementById('labDelta').textContent = (delta >= 0 ? '+' : '') + (delta * 100).toFixed(1) + '% vs. indicative price · UNSIGNED';
  }}
  [growth, discount, terminal].forEach(input => input.addEventListener('input', updateDcf)); updateDcf();
}})();
</script>
</body></html>"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")
    return destination
