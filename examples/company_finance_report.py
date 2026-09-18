"""Portable HTML and Markdown reports for company-parameterized financial runs."""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Mapping


def _escape(value: Any) -> str:
    return html.escape(str(value if value is not None else "Not available"), quote=True)


def _format(value: Any, unit: str = "") -> str:
    if value is None:
        return "Not available"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if unit == "percent":
        return f"{value:.1%}"
    if unit == "multiple":
        return f"{value:.2f}x"
    if isinstance(value, (int, float)):
        return f"{value:,.3f}"
    return _escape(value)


def _table(rows: list, columns: list) -> str:
    heading = "".join(f"<th scope='col'>{_escape(label)}</th>" for key, label in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{_format(row.get(key))}</td>" for key, label in columns) + "</tr>"
        for row in rows
    )
    return f"<div class='table-scroll'><table><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table></div>" if rows else "<p class='muted'>No supported observations available.</p>"


def _chart(series: Mapping[str, Mapping[str, Any]], currency: str, title: str) -> str:
    periods = sorted({period for values in series.values() for period in values})[-12:]
    points = [values[period] for values in series.values() for period in periods if values.get(period) is not None]
    if not periods or not points:
        return "<p class='muted'>No comparable quarterly chart available.</p>"
    lower, upper = min(0, min(points)), max(0, max(points))
    span = upper - lower or 1
    colors = ("#4cc9f0", "#8ce99a", "#ffd166", "#ff8787")
    geometry = []
    for step in range(5):
        height = 24 + step * 196 / 4
        value = upper - step * span / 4
        geometry.append(f"<line class='gridline' x1='56' x2='850' y1='{height}' y2='{height}'/><text x='47' y='{height + 4}' text-anchor='end'>{value:.0f}</text>")
    for index, period in enumerate(periods):
        horizontal = 56 + index * 794 / max(1, len(periods) - 1)
        geometry.append(f"<text x='{horizontal}' y='246' text-anchor='middle'>{_escape(period.replace('FY', ''))}</text>")
    legend = []
    for index, (label, values) in enumerate(series.items()):
        path = []
        circles = []
        for position, period in enumerate(periods):
            if values.get(period) is None:
                if path:
                    geometry.append(f"<polyline points='{' '.join(path)}' fill='none' stroke='{colors[index % 4]}' stroke-width='2.5'/>")
                    path = []
                continue
            horizontal = 56 + position * 794 / max(1, len(periods) - 1)
            vertical = 24 + (upper - values[period]) / span * 196
            path.append(f"{horizontal},{vertical}")
            circles.append(f"<circle cx='{horizontal}' cy='{vertical}' r='3' fill='{colors[index % 4]}'><title>{_escape(label)} {_escape(period)}: {values[period]:,.3f} {_escape(currency)} billion</title></circle>")
        if path:
            geometry.append(f"<polyline points='{' '.join(path)}' fill='none' stroke='{colors[index % 4]}' stroke-width='2.5'/>")
        geometry.extend(circles)
        legend.append(f"<span><i style='background:{colors[index % 4]}'></i>{_escape(label)}</span>")
    return f"<figure class='viz'><figcaption>{_escape(title)} <small>{_escape(currency)} billions</small></figcaption><div class='legend'>{''.join(legend)}</div><div class='chart-scroll'><svg viewBox='0 0 880 266' role='img' aria-label='{_escape(title)}'>{''.join(geometry)}</svg></div></figure>"


def _executive_overview(package: Mapping[str, Any]) -> str:
    data = package["data"]
    governance = package["governance"]
    reconciliations = data["reconciliation"]
    passed = sum(bool(check["within_tolerance"]) for check in reconciliations)
    missing = len(data["coverage"]["missing_metrics"])
    gaps = len(data["coverage"]["retrieval_gaps"])
    observations = package["synthesis"]
    quality_rows = [
        ("Annual / quarter reconciliation", f"{passed} / {len(reconciliations)}", "pass" if reconciliations and passed == len(reconciliations) else "watch"),
        ("Available analytical measures", str(observations["available_observations"]), "neutral"),
        ("Unavailable analytical measures", str(observations["unavailable_observations"]), "watch" if observations["unavailable_observations"] else "neutral"),
        ("Missing annual fact mappings", str(missing), "watch" if missing else "neutral"),
        ("Source retrieval gaps", str(gaps), "watch" if gaps else "pass"),
    ]
    rows = "".join(
        f"<div class='quality-row'><span>{_escape(label)}</span><b class='tone-{tone}'>{_escape(value)}</b></div>"
        for label, value, tone in quality_rows
    )
    focus = "".join(f"<li>{_escape(text)}</li>" for text in observations["review_focus"])
    return f"""<section id="cockpit" class="executive-section">
      <div class="section-head"><div><span class="section-number">01 / EXECUTIVE COCKPIT</span><h2>Financial position and review priorities</h2></div><span class="section-tag">CONSOLIDATED / {_escape(data['currency'])}</span></div>
      <div class="executive-grid"><div class="executive-summary"><span class="overline">GOVERNED RESEARCH SYNTHESIS</span><p>{_escape(observations['summary'])}</p><h3>Professional review priorities</h3><ol>{focus}</ol></div>
      <div class="quality-summary"><span class="overline">EVIDENCE COMPLETENESS</span>{rows}<p class="muted">Completeness is not a probability of accuracy. Review status: <strong>{_escape(governance['review']['status'])}</strong>.</p></div></div>
    </section>"""


def render_company_report(package: Mapping[str, Any], output: Path) -> Path:
    data = package["data"]
    company = data["company"]
    governance = package["governance"]
    review = governance["review"]
    currency = data["currency"]
    sources = package["sources"]
    quarter_metrics = ("revenue", "operating_income", "operating_cash_flow", "capex", "free_cash_flow")
    labels = ("Revenue", "Operating income", "Operating cash flow", "Capex", "Free cash flow")
    quarterly = data["quarterly"]
    periods = sorted({period for metric in quarter_metrics for period in quarterly.get(metric, {})})
    quarter_rows = [{"period": period, **{metric: quarterly.get(metric, {}).get(period) for metric in quarter_metrics}} for period in periods]
    annual_periods = sorted({
        period for metric in quarter_metrics for period in data["annual"].get(metric, {})
    })[-5:]
    annual_rows = []
    for period in annual_periods:
        row = {"period": period, **{metric: data["annual"].get(metric, {}).get(period) for metric in quarter_metrics}}
        cash, capex = row["operating_cash_flow"], row["capex"]
        row["free_cash_flow"] = cash - capex if cash is not None and capex is not None else None
        annual_rows.append(row)
    columns = [("period", "Fiscal period"), *zip(quarter_metrics, labels)]
    analysis_panels = []
    markdown = [
        f"# {company['name']} - Financial Intelligence", "",
        f"CIK: {company['cik']} | Tickers: {', '.join(company['tickers'])} | Currency: {currency}",
        f"Generated: {package['generated_at']} | Mode: {package['mode']}", "",
        package["synthesis"]["summary"], "",
        "> Controlled pilot. Consolidated public financials, not investment, tax, audit, or legal advice.", "",
    ]
    for analysis_index, analysis in enumerate(package["analyses"], 1):
        record = analysis["output"]
        findings = []
        markdown.extend([f"## {record['title']}", "", f"Coverage: {record['status']}", "", "| Measure | Value | Unit | Period |", "|---|---:|---|---|"])
        for finding in record["findings"]:
            links = " ".join(
                f"<a href='#{_escape(fact_id)}' title='{_escape(fact_id)}'>Fact {index}</a>"
                for index, fact_id in enumerate(finding["fact_ids"], 1)
            )
            source_links = " ".join(f"<a href='#{_escape(source_id)}'>{_escape(source_id)}</a>" for source_id in finding["source_ids"])
            findings.append(
                f"<tr><th scope='row'>{_escape(finding['label'])}<small>{_escape(finding['period'])}</small></th>"
                f"<td>{_format(finding['value'], finding['unit'])}</td><td>{_escape(finding['unit'])}</td>"
                f"<td><div class='refs'>{links} {source_links}</div><small>{_escape(finding['limitation'])}</small></td></tr>"
            )
            markdown.append(f"| {finding['label']} | {_format(finding['value'], finding['unit'])} | {finding['unit']} | {finding['period']} |")
        search = " ".join([record["title"], *(finding["label"] for finding in record["findings"])]).lower()
        analysis_panels.append(
            f"<article class='analysis' data-search='{_escape(search)}' data-status='{_escape(record['status'])}'>"
            f"<div class='specialist-top'><div><span class='overline'>SPECIALIST {analysis_index:02d} / {_escape(record['component'].replace('_', ' ').upper())}</span>"
            f"<h3>{_escape(record['title'])}</h3></div><span class='coverage-state'>{_escape(record['status'])}</span></div>"
            f"<div class='table-scroll'><table><thead><tr><th>Measure</th><th>Value</th><th>Unit</th><th>Evidence</th></tr></thead><tbody>{''.join(findings)}</tbody></table></div>"
            f"<p class='muted'>Signed execution: <code>{_escape(analysis['evidence_id'])}</code></p></article>"
        )
        markdown.extend(["", f"Signed execution: `{analysis['evidence_id']}`", ""])
    facts = []
    for fact_id, fact in sorted(data["fact_index"].items(), key=lambda item: (str(item[1].get("period")), str(item[1].get("metric")))):
        inputs = " ".join(f"<a href='#{_escape(item['fact_id'])}'>{_escape(item['fact_id'])}</a>" for item in fact.get("inputs", []))
        recasts = "".join(f"<li>{_escape(item.get('filed'))}: {_escape(item.get('value'))} | {_escape(item.get('accession'))}{' | selected' if item.get('selected') else ''}</li>" for item in fact.get("recast_chain", []))
        search = " ".join(str(fact.get(key, "")) for key in ("metric", "period", "concept", "accession")) + " " + fact_id
        facts.append(
            f"<details id='{_escape(fact_id)}' data-search='{_escape(search.lower())}'><summary>{_escape(fact.get('metric'))} / {_escape(fact.get('period'))}"
            f"<strong>{_format(fact.get('value'))} {_escape(fact.get('normalized_unit'))}</strong></summary>"
            f"<div class='fact-body'><p><code>{_escape(fact_id)}</code></p><dl>"
            f"<dt>Concept</dt><dd>{_escape(fact.get('concept'))}</dd><dt>Filed</dt><dd>{_escape(fact.get('filed'))}</dd>"
            f"<dt>Accession</dt><dd>{_escape(fact.get('accession'))}</dd><dt>Period dates</dt><dd>{_escape(fact.get('start'))} to {_escape(fact.get('end'))}</dd>"
            f"<dt>Method</dt><dd>{_escape(fact.get('formula') or fact.get('selection_method'))}</dd>"
            f"<dt>Source</dt><dd><a href='#{_escape(fact.get('source_id'))}'>{_escape(fact.get('source_id'))}</a></dd></dl>"
            f"<div class='refs'>{inputs}</div><ul>{recasts}</ul></div></details>"
        )
    source_rows = "".join(
        f"<tr id='{_escape(source_id)}'><th scope='row'>{_escape(source_id)}</th><td><a href='{_escape(source['url'])}' target='_blank' rel='noopener noreferrer'>{_escape(source['description'])}</a></td>"
        f"<td>{_escape(source['retrieved_at'])}<small>{_escape(source.get('freshness'))} / {_escape(source.get('retrieval_mode'))}</small></td>"
        f"<td class='hash'><code>{_escape(source['sha256'])}</code></td></tr>"
        for source_id, source in sources.items()
    )
    timeline_rows = "".join(
        f"<tr><td>{_escape(filing['filingDate'])}</td><td>{_escape(filing['form'])}</td><td>{_escape(filing['reportDate'])}</td>"
        f"<td><a href='{_escape(filing['url'])}' target='_blank' rel='noopener noreferrer'>{_escape(filing['accessionNumber'])}</a></td></tr>"
        for filing in data["filing_timeline"]
    )
    votes = {vote["reviewer"]: vote["decision"] for vote in review["votes"]}
    reviewer_rows = "".join(f"<li><strong>{_escape(reviewer)}</strong><span>{_escape(votes.get(reviewer, 'pending'))}</span></li>" for reviewer in review["required_reviewers"])
    limitations = "".join(f"<li>{_escape(text)}</li>" for text in data["coverage"]["limitations"])
    missing = _table(data["coverage"]["missing_metrics"], [("section", "Statement"), ("metric", "Metric"), ("reason", "Coverage limitation")])
    checks = _table(data["reconciliation"], [("metric", "Metric"), ("fiscal_year", "Fiscal year"), ("quarter_sum", "Quarter sum"), ("annual_value", "Filed annual"), ("variance", "Variance"), ("within_tolerance", "Within tolerance")])
    kpis = "".join(
        f"<div class='kpi'><span>{_escape(label)}</span><strong>{_format(data['ttm'].get(metric), unit)}</strong>"
        f"<small>{_escape(currency) + ' billions' if unit != 'percent' else 'Percent of revenue'} / TTM</small></div>"
        for label, metric, unit in (
            ("Revenue", "revenue", ""), ("Operating margin", "operating_margin", "percent"),
            ("Operating cash flow", "operating_cash_flow", ""), ("Capex", "capex", ""),
            ("Free cash flow", "free_cash_flow", ""), ("FCF margin", "free_cash_flow_margin", "percent"),
        )
    )
    executive = _executive_overview(package)
    annual_chart = _chart(
        {label: {row['period']: row[metric] for row in annual_rows} for label, metric in (
            ("Revenue", "revenue"), ("Operating cash flow", "operating_cash_flow"),
            ("Capex", "capex"), ("Free cash flow", "free_cash_flow"),
        )},
        currency, "Annual revenue and cash generation",
    )
    ttm_chart = _chart(
        {label: data['ttm_series'].get(metric, {}) for label, metric in (
            ("Revenue", "revenue"), ("Operating cash flow", "operating_cash_flow"), ("Capex", "capex"), ("Free cash flow", "free_cash_flow"),
        )}, currency, "Rolling trailing-twelve-month results",
    )
    quarterly_chart = _chart({"Revenue": quarterly.get("revenue", {}), "Operating cash flow": quarterly.get("operating_cash_flow", {}), "Capex": quarterly.get("capex", {}), "Free cash flow": quarterly.get("free_cash_flow", {})}, currency, "Quarterly financial history")
    gap_note = "".join(f"<li>{_escape(gap['source_id'])}: {_escape(gap['reason'])}</li>" for gap in data["coverage"]["retrieval_gaps"])
    proofs = "".join(
        f"<li><span class='proof-state {'tone-pass' if proof['holds'] else 'tone-risk'}'>{'PASS' if proof['holds'] else 'FAIL'}</span>"
        f"<div><strong>{_escape(proof['label'])}</strong><small>{_escape(proof['reason'])}</small></div></li>"
        for proof in governance['guarantees']
    )
    budget_rows = "".join(
        f"<div class='quality-row'><span>{_escape(name.replace('_', ' ').title())}</span><strong>{_escape(value)}</strong></div>"
        for name, value in governance.get('budget', {}).items()
    )
    audit_checks = "".join(
        f"<li><span class='proof-state {'tone-pass' if passed else 'tone-risk'}'>{'PASS' if passed else 'FAIL'}</span><span>{_escape(name.title())}</span></li>"
        for name, passed in governance['package_validation']['checks'].items()
    )
    comments = "".join(
        f"<li><strong>{_escape(comment['reviewer'])}</strong><p>{_escape(comment['comment'])}</p></li>"
        for comment in review.get('comments', [])
    ) or "<li class='muted'>No review comments recorded.</li>"
    review_tone = 'pass' if review['approval_valid'] else 'risk' if review['status'] == 'rejected' else 'watch'
    source_modes = {source.get('retrieval_mode', '') for source in sources.values()}
    freshness = 'STALE FALLBACK' if 'cache-fallback' in source_modes else 'VERIFIED CACHE' if package['mode'] == 'cache-only' else 'LIVE / CACHE'
    document = f"""<!doctype html>
<html lang="en" data-theme="dark"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light"><meta name="description" content="Governed consolidated financial report for {_escape(company['name'])}">
<title>{_escape(company['name'])} | Autarch Finance Intelligence</title>
<style>
:root{{--paper:#061018;--subtle:#0b1923;--panel:#102431;--ink:#edf7ff;--muted:#a3b8c6;--line:#294251;--accent:#4cc9f0;--green:#8ce99a;--gold:#ffd166;--risk:#ff8787;--radius:8px;color-scheme:dark}}
*{{box-sizing:border-box;letter-spacing:0}}html{{scroll-behavior:smooth;scroll-padding-top:92px}}body{{margin:0;color:var(--ink);background:linear-gradient(165deg,#0d2530 0,transparent 620px),var(--paper);font:14px/1.6 'Bahnschrift','Trebuchet MS',sans-serif}}a{{color:var(--accent);text-underline-offset:3px}}button,input,select{{font:inherit}}button,a.download{{border:1px solid var(--line);border-radius:5px;padding:9px 13px;background:var(--panel);text-decoration:none;color:var(--ink);cursor:pointer}}button:hover,a.download:hover{{border-color:var(--accent)}}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{{outline:2px solid var(--accent);outline-offset:4px}}.wrap{{width:calc(100% - 48px);max-width:1260px;margin:auto}}header{{position:sticky;top:0;z-index:20;border-bottom:1px solid var(--line);background:rgba(6,16,24,.97)}}.topbar{{min-height:72px;display:flex;gap:24px;align-items:center;justify-content:space-between;padding-block:12px}}.brand{{font-size:13px;font-weight:700;white-space:nowrap}}.brand b{{color:var(--accent)}}nav{{display:flex;gap:18px;flex-wrap:wrap;font-size:12px}}nav a{{text-decoration:none;color:var(--muted)}}nav a:hover{{color:var(--accent)}}.nav-actions{{display:flex;gap:8px;font-size:12px;flex-shrink:0}}.identity{{display:flex;align-items:center;justify-content:space-between;gap:28px;padding-block:34px 24px}}.identity>div{{min-width:0}}.overline,.section-number{{font-size:11px;font-weight:600;color:var(--accent)}}.section-number{{color:var(--gold)}}h1,h2,h3{{font-family:'Bahnschrift','Trebuchet MS',sans-serif;font-weight:600}}h1{{font-size:42px;line-height:1.12;margin:8px 0 12px;overflow-wrap:anywhere}}h2{{font-size:27px;line-height:1.22;margin:7px 0 0}}h3{{font-size:19px;line-height:1.3;margin:4px 0 14px}}p{{margin-block:10px 16px}}small{{display:block;font-size:11px;color:var(--muted)}}.muted{{color:var(--muted)}}.status{{text-align:right;max-width:330px}}.status>b{{display:block;font-size:18px;text-transform:uppercase}}.status small{{margin-top:5px;overflow-wrap:anywhere}}.identity-meta{{font-size:12px;color:var(--muted)}}.identity-meta small{{margin-top:4px}}.run-facts{{display:flex;flex-wrap:wrap;gap:9px 22px;margin-top:17px;font-size:11px;color:var(--muted)}}.run-facts strong{{color:var(--ink);margin-right:4px}}
.kpis{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin-bottom:14px;font-variant-numeric:tabular-nums}}.kpi{{border:1px solid var(--line);border-top:2px solid var(--accent);border-radius:var(--radius);background:var(--subtle);padding:16px 13px;min-width:0}}.kpi:nth-child(2n){{border-top-color:var(--green)}}.kpi:nth-child(3n){{border-top-color:var(--gold)}}.kpi>span{{font-size:11px;color:var(--muted)}}.kpi strong{{display:block;font-size:25px;line-height:1.2;font-weight:600;margin-block:10px;overflow-wrap:anywhere}}.kpi small{{font-size:10px}}section{{padding-block:38px;border-bottom:1px solid var(--line)}}.section-head{{display:flex;align-items:end;justify-content:space-between;gap:20px;margin-bottom:23px}}.section-head>div{{min-width:0}}.section-tag{{font-size:10px;color:var(--muted);text-align:right}}.executive-grid{{display:grid;grid-template-columns:1.2fr 1fr;gap:40px}}.executive-grid>div,.chart-grid>*,.review-grid>*,.governance-grid>*{{min-width:0}}.executive-summary>p{{font-size:16px;line-height:1.65}}.executive-summary ol{{padding-left:18px;color:var(--muted);font-size:13px}}.executive-summary li{{margin-block:9px}}.quality-summary{{border-left:1px solid var(--line);padding-left:28px}}.quality-row{{display:flex;justify-content:space-between;gap:16px;border-bottom:1px solid var(--line);padding-block:11px;font-size:12px}}.quality-row span{{color:var(--muted)}}.quality-row b,.quality-row strong{{font-variant-numeric:tabular-nums;text-align:right}}.quality-summary>p{{font-size:11px}}.tone-pass{{color:var(--green)}}.tone-watch{{color:var(--gold)}}.tone-risk{{color:var(--risk)}}.tone-neutral{{color:var(--ink)}}
.chart-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}}figure{{margin:0 0 20px;padding:18px;border:1px solid var(--line);border-radius:var(--radius);background:var(--subtle)}}figcaption{{font-size:14px;font-weight:600;display:flex;justify-content:space-between;gap:15px}}figcaption small{{font-size:10px;white-space:nowrap}}.legend{{display:flex;gap:8px 16px;flex-wrap:wrap;padding-block:12px;font-size:11px;color:var(--muted)}}.legend i{{display:inline-block;width:8px;height:8px;margin-right:6px;border-radius:2px}}.chart-scroll,.table-scroll{{max-width:100%;overflow:auto;scrollbar-color:var(--line) var(--subtle)}}.chart-scroll svg{{display:block;width:100%;min-width:540px;max-height:350px}}svg text{{fill:var(--muted);font:11px 'Trebuchet MS',sans-serif}}.gridline{{stroke:var(--line);stroke-width:1}}.table-scroll{{border:1px solid var(--line);border-radius:var(--radius);background:var(--subtle)}}table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}th,td{{text-align:left;padding:11px 14px;border-bottom:1px solid var(--line);vertical-align:top}}thead th{{font-size:10px;color:var(--muted);background:var(--panel);white-space:nowrap;text-transform:uppercase}}tbody th{{font-size:12px;font-weight:500}}td{{font-size:12px}}td:not(:last-child){{white-space:nowrap}}tbody tr:last-child td,tbody tr:last-child th{{border-bottom:0}}tbody tr:hover{{background:var(--panel)}}td small,th small{{margin-top:4px}}.analysis{{border:1px solid var(--line);border-radius:var(--radius);background:linear-gradient(135deg,var(--panel),var(--subtle));padding:22px;margin-bottom:18px;min-width:0}}.analysis .table-scroll{{border:0;border-radius:0;background:transparent}}.analysis thead th{{background:transparent}}.analysis td:last-child{{min-width:200px}}.analysis>p{{margin:14px 0 0;font-size:11px}}.specialist-top{{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:10px}}.specialist-top>div{{min-width:0}}.specialist-top h3{{font-size:22px;margin-top:5px}}.coverage-state{{color:var(--green);font-size:11px;text-transform:uppercase;flex-shrink:0}}.analysis[data-status='partial'] .coverage-state{{color:var(--gold)}}.analysis[data-status='unavailable'] .coverage-state{{color:var(--risk)}}.analysis-tools{{display:flex;gap:10px;align-items:center;margin-bottom:18px;flex-wrap:wrap}}.analysis-tools output{{margin-left:auto;color:var(--muted);font-size:12px}}input[type=search],select{{padding:10px 12px;border:1px solid var(--line);border-radius:5px;background:var(--subtle);color:var(--ink)}}input[type=search]{{width:100%;max-width:390px;min-width:0}}input::placeholder{{color:var(--muted)}}select{{max-width:100%}}.refs{{display:flex;flex-wrap:wrap;gap:6px 12px;font-size:11px;overflow-wrap:anywhere}}.refs a{{max-width:100%;overflow-wrap:anywhere}}code{{font:11px/1.6 Consolas,monospace;overflow-wrap:anywhere}}.hash{{min-width:180px;max-width:240px;overflow-wrap:anywhere}}
.fact-list{{max-height:640px;overflow:auto;padding:3px;scrollbar-color:var(--line) var(--subtle)}}details{{border:1px solid var(--line);border-radius:5px;scroll-margin-top:100px;min-width:0;background:var(--subtle);margin-bottom:6px}}summary{{cursor:pointer;display:flex;justify-content:space-between;gap:16px;padding:13px;font-size:12px;overflow-wrap:anywhere}}summary strong{{text-align:right;color:var(--accent)}}details:target{{outline:2px solid var(--accent)}}.fact-body{{border-top:1px solid var(--line);padding:15px;overflow-wrap:anywhere;font-size:12px;color:var(--muted)}}.fact-body p{{margin-top:0}}dl{{display:grid;grid-template-columns:auto minmax(0,1fr);gap:6px 14px}}dd{{margin:0}}dt{{color:var(--ink)}}.review-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:36px}}.review-decision{{border-left:3px solid var(--gold);padding-left:20px}}.review-decision p{{font-size:13px}}.review-decision .review-state{{font-size:26px;line-height:1.2;text-transform:uppercase;margin-top:8px}}.review-verdict{{border-top:1px solid var(--line);padding-top:15px;margin-top:22px;color:var(--gold);font-size:11px;text-transform:uppercase}}.reviewers,.review-comments{{list-style:none;padding:0}}.reviewers li{{display:flex;justify-content:space-between;gap:14px;border-bottom:1px solid var(--line);padding-block:10px;font-size:12px}}.reviewers span{{color:var(--gold);text-transform:uppercase}}.review-comments{{font-size:12px;color:var(--muted)}}.review-comments p{{margin-block:4px 12px}}.governance-grid{{display:grid;grid-template-columns:1.2fr 1fr;gap:36px}}.proofs{{padding:0;list-style:none}}.proofs li{{display:flex;gap:16px;border-bottom:1px solid var(--line);padding-block:12px;align-items:baseline;font-size:12px}}.proof-state{{font:11px Consolas,monospace;flex-shrink:0}}.proofs strong{{font-size:13px}}.proofs small{{margin-top:4px}}.audit-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0 22px}}.budget-summary{{padding-left:26px;border-left:1px solid var(--line)}}.limitations{{color:var(--muted);font-size:13px;padding-left:20px}}.limitations li{{margin-block:8px}}.scope-note{{border-left:3px solid var(--gold);padding-left:14px;color:var(--muted);font-size:12px}}.downloads{{display:flex;gap:10px;flex-wrap:wrap;margin-top:22px;font-size:12px}}footer{{padding-block:24px;background:var(--subtle);overflow-wrap:anywhere;font-size:12px;color:var(--muted)}}footer small{{margin-top:8px}}[hidden]{{display:none!important}}.empty{{padding:20px;border:1px dashed var(--line);color:var(--muted)}}.skip{{position:absolute;left:-9999px}}.skip:focus{{left:12px;background:var(--paper);padding:10px;z-index:40}}
@media(prefers-reduced-motion:no-preference){{.identity,.kpis{{animation:appear .3s ease-out}}@keyframes appear{{from{{opacity:0;transform:translateY(5px)}}to{{opacity:1;transform:none}}}}}}
@media(max-width:1100px){{.topbar{{flex-wrap:wrap;gap:12px}}nav{{order:3;width:100%;padding-top:2px}}.kpis{{grid-template-columns:repeat(3,minmax(0,1fr))}}.executive-grid{{gap:24px}}.chart-grid{{grid-template-columns:1fr}}html{{scroll-padding-top:126px}}}}
@media(max-width:680px){{header{{position:static}}html{{scroll-padding-top:15px}}.wrap{{width:calc(100% - 24px)}}.identity{{display:block;padding-block:25px 18px}}.status{{text-align:left;max-width:none;border-top:1px solid var(--line);margin-top:16px;padding-top:12px}}.status>b{{font-size:16px}}h1{{font-size:34px}}h2{{font-size:23px}}.brand{{font-size:12px;white-space:normal}}.nav-actions{{font-size:11px}}nav{{gap:8px 16px}}.kpis{{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}.kpi{{padding:13px 11px}}.kpi strong{{font-size:23px}}.executive-grid,.review-grid,.governance-grid{{grid-template-columns:1fr;gap:22px}}.quality-summary,.budget-summary{{border-left:0;padding-left:0}}.section-head{{display:block}}.section-head input{{margin-top:14px}}.section-tag{{display:block;text-align:left;margin-top:12px}}.analysis{{padding:14px}}.specialist-top{{display:block}}.specialist-top h3{{font-size:21px}}.coverage-state{{display:block;margin-block:8px 14px}}.analysis-tools input{{max-width:none}}.analysis-tools output{{margin-left:0}}summary{{display:block}}summary strong{{display:block;text-align:left;margin-top:6px}}.reviewers li{{flex-wrap:wrap}}.audit-grid{{grid-template-columns:1fr}}.review-decision{{padding-left:13px}}section{{padding-block:27px}}figure{{padding:13px}}figcaption{{display:block}}figcaption small{{margin-top:6px}}}}
@media print{{:root{{--paper:#fff;--subtle:#f4f6f8;--panel:#edf0f3;--ink:#17232c;--muted:#485c69;--line:#cfd9e0;--accent:#075b84;--green:#235b36;--gold:#705100;--risk:#8f2323;color-scheme:light}}header,input,select,button,.analysis-tools,.downloads,.skip,.empty{{display:none!important}}html{{scroll-behavior:auto}}.wrap{{width:100%;max-width:none}}body{{background:white;font-size:10pt}}h1{{font-size:26px}}h2{{font-size:20px}}.identity,.kpis{{animation:none}}.fact-list{{max-height:none;overflow:visible}}.table-scroll,.chart-scroll{{overflow:visible;border-radius:0}}.chart-scroll svg{{min-width:0;max-height:none}}.chart-grid,.executive-grid,.review-grid,.governance-grid{{grid-template-columns:1fr}}.audit-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.kpis{{grid-template-columns:repeat(3,minmax(0,1fr))}}.kpi strong{{font-size:20px}}th,td{{padding:5px;font-size:8pt;white-space:normal!important;overflow-wrap:anywhere}}.analysis td:last-child,.hash{{min-width:0}}.analysis{{background:white;padding:10px}}figure{{break-inside:avoid}}section{{padding-block:18px}}.quality-summary,.budget-summary{{padding-left:0;border-left:0}}.review-verdict{{color:var(--risk)}}a{{text-decoration:none}}}}
</style></head><body><a class="skip" href="#main">Skip to financial report</a>
<header><div class="wrap topbar"><div class="brand"><b>AUTARCH</b> / FINANCE INTELLIGENCE</div><nav aria-label="Report sections"><a href="#cockpit">Cockpit</a><a href="#financials">Financials</a><a href="#analyses">Workbench</a><a href="#facts">Lineage</a><a href="#review">Review</a></nav><div class="nav-actions"><a class="download" href="evidence_bundle.zip" download>Evidence bundle</a><button type="button" id="printReport" title="Print report or save as PDF">Print / PDF</button></div></div></header>
<main id="main" class="wrap"><div class="identity"><div><span class="overline">QUARTERLY &amp; CHANGE INTELLIGENCE</span><h1>{_escape(company['name'])}</h1><div class="identity-meta">{_escape(', '.join(company['tickers']))} | CIK {_escape(company['cik'])} | {_escape(currency)}<small>Annual period {_escape(data['latest_annual_end'])} | Latest report {_escape(data['latest_report_end'])} | Fiscal year-end {_escape(data['fiscal_year_end'])}</small></div><div class="run-facts"><span><strong>{len(sources)}</strong> SOURCE OBJECTS</span><span><strong>{len(package['analyses'])}</strong> SPECIALISTS</span><span><strong>{len(data['fact_index'])}</strong> INDEXED FACTS</span><span>{freshness}</span></div></div><div class="status"><span class="overline">RELEASE REVIEW</span><b class="tone-{review_tone}">{_escape(review['status'])}</b><small>Consolidated financials / controlled pilot</small><small>{_escape(package['generated_at'])}</small></div></div>
<div class="kpis" aria-label="Trailing twelve month key financial measures">{kpis}</div>{executive}
<section id="financials"><div class="section-head"><div><span class="section-number">02 / FINANCIAL ENGINE</span><h2>Scale, earnings, and cash conversion</h2></div><span class="section-tag">TTM THROUGH {_escape(data.get('ttm_as_of'))}</span></div><div class="chart-grid">{annual_chart}{quarterly_chart}</div>{ttm_chart}<h3>Annual results / {_escape(currency)} billions</h3>{_table(annual_rows, columns)}<h3 style="margin-top:22px">Quarterly results / {_escape(currency)} billions</h3>{_table(quarter_rows, columns)}</section>
<section id="analyses"><div class="section-head"><div><span class="section-number">03 / SPECIALIST WORKBENCH</span><h2>Financial analyses</h2></div><span class="section-tag">GENERAL SEC FINANCIAL PROFILE</span></div><div class="analysis-tools"><input type="search" id="analysisSearch" aria-label="Filter financial analyses" placeholder="Search specialists and financial measures"><select id="analysisStatus" aria-label="Filter analysis coverage"><option value="all">All coverage</option><option value="available">Available</option><option value="partial">Partial</option><option value="unavailable">Unavailable</option></select><output id="analysisCount" aria-live="polite">{len(package['analyses'])} analyses</output></div>{''.join(analysis_panels)}<p id="analysisEmpty" class="empty" hidden>No matching analyses.</p></section>
<section id="filings"><div class="section-head"><div><span class="section-number">04 / FILING TIMELINE</span><h2>Disclosures and reconciliation</h2></div><span class="section-tag">SEC FILING SEQUENCE</span></div><div class="table-scroll"><table><thead><tr><th>Filed</th><th>Form</th><th>Report period</th><th>Accession / filed source</th></tr></thead><tbody>{timeline_rows}</tbody></table></div><h3 style="margin-top:22px">Quarter-to-annual reconciliation</h3>{checks}</section>
<section id="facts"><div class="section-head"><div><span class="section-number">05 / FACT LINEAGE</span><h2>Selected facts / {len(data['fact_index'])}</h2></div><input id="factSearch" type="search" aria-label="Filter selected facts" placeholder="Metric, fiscal period, concept, or accession"></div><div class="fact-list">{''.join(facts)}</div><p id="factEmpty" class="empty" hidden>No matching facts.</p></section>
<section id="sources"><div class="section-head"><div><span class="section-number">06 / SOURCE EVIDENCE</span><h2>Evidence manifest</h2></div><span class="section-tag">{len(sources)} SOURCE OBJECTS</span></div><div class="table-scroll"><table><thead><tr><th>Source</th><th>Document</th><th>Retrieved / freshness</th><th>SHA-256</th></tr></thead><tbody>{source_rows}</tbody></table></div></section>
<section id="review"><div class="section-head"><div><span class="section-number">07 / ACCOUNTABLE REVIEW</span><h2>Digest-bound release control</h2></div><span class="section-tag">NAMED TWO-PERSON REVIEW</span></div><div class="review-grid"><div class="review-decision"><span class="overline">CURRENT REVIEW STATE</span><p class="review-state tone-{review_tone}">{_escape(review['status'])}</p><p>Request <code>{_escape(review['id'])}</code></p><p>Content digest <code>{_escape(governance['release_digest'])}</code></p><small>Exact content match: {_format(review['digest_matches'])} | Approval valid: {_format(review['approval_valid'])}</small><div class="review-verdict">No external publication or trading authority is granted.</div></div><div><h3>Named reviewers</h3><ul class="reviewers">{reviewer_rows}</ul><p class="muted">{_escape(governance['identity_assurance'])}</p><h3>Review comments</h3><ul class="review-comments">{comments}</ul></div></div></section>
<section id="governance"><div class="section-head"><div><span class="section-number">08 / GOVERNANCE PROOF</span><h2>Authority, budgets, and evidence integrity</h2></div></div><div class="governance-grid"><div><h3>Static guarantees</h3><ul class="proofs">{proofs}</ul><div class="quality-row"><span>Signed action chain / {governance['action_chain']['records']} records</span><strong>{'VERIFIED' if governance['action_chain']['verified'] else 'FAILED'}</strong></div><div class="quality-row"><span>Review event chain</span><strong>{'VERIFIED' if governance['review_chain']['verified'] else 'FAILED'}</strong></div></div><div class="budget-summary"><h3>Shared economic envelope</h3>{budget_rows}</div></div><h3 style="margin-top:24px">Package acceptance checks</h3><ul class="proofs audit-grid">{audit_checks}</ul></section>
<section id="coverage"><div class="section-head"><div><span class="section-number">09 / METHODOLOGY &amp; DELIVERY</span><h2>Coverage and limitations</h2></div><span class="section-tag">CONTROLLED PILOT</span></div><ul class="limitations">{limitations}</ul>{f'<ul class="limitations">{gap_note}</ul>' if gap_note else ''}<h3>Unavailable mapped metrics</h3>{missing}<p class="scope-note">Subsidiary-level statements are not included unless separately reported and mapped. Ownership alone does not provide standalone figures. The general profile has six analyses; company-specific valuations, peers, and operating KPIs are not included.</p><div class="downloads"><a class="download" href="financial_report.json" download>JSON package</a><a class="download" href="financial_report.md" download>Detailed report</a><a class="download" href="release_content.json" download>Review content</a><a class="download" href="review_audit.json" download>Review audit</a><a class="download" href="signed_audit.jsonl" download>Signed action audit</a><a class="download" href="evidence_bundle.zip" download>Evidence bundle</a></div></section></main>
<footer><div class="wrap">Controlled pilot. Not investment, audit, tax, credit, or legal advice. Accountable professional review remains required.<small>Run {_escape(package['run_id'])} | {_escape(package['schema_version'])}</small></div></footer>
<script>
function filterItems(input, selector) {{
  const terms = input.value.trim().toLowerCase().split(/\\s+/).filter(Boolean);
    const isAnalysis = selector === '.analysis';
    const selectedStatus = isAnalysis ? document.getElementById('analysisStatus').value : 'all';
    let visibleCount = 0;
    document.querySelectorAll(selector).forEach(item => {{
        item.hidden = terms.some(term => !item.dataset.search.includes(term)) || (selectedStatus !== 'all' && item.dataset.status !== selectedStatus);
        if (!item.hidden) visibleCount++;
    }});
    document.getElementById(isAnalysis ? 'analysisEmpty' : 'factEmpty').hidden = visibleCount !== 0;
    if (isAnalysis) document.getElementById('analysisCount').textContent = visibleCount + ' analyses';
}}
const analysisSearch = document.getElementById('analysisSearch');
const factSearch = document.getElementById('factSearch');
analysisSearch.addEventListener('input', () => filterItems(analysisSearch, '.analysis'));
document.getElementById('analysisStatus').addEventListener('change', () => filterItems(analysisSearch, '.analysis'));
factSearch.addEventListener('input', () => filterItems(factSearch, '.fact-list details'));
function revealAnchor() {{
  const target = document.getElementById(location.hash.slice(1));
  if (target && target.tagName === 'DETAILS') {{
    factSearch.value = ''; filterItems(factSearch, '.fact-list details');
    target.open = true; target.scrollIntoView({{block:'center'}});
  }}
}}
window.addEventListener('hashchange', revealAnchor); revealAnchor();
document.getElementById('printReport').addEventListener('click', () => window.print());
let printState = [];
window.addEventListener('beforeprint', () => {{
  printState = [...document.querySelectorAll('.analysis, .fact-list details')].map(item => ({{item, hidden:item.hidden, open:item.open}}));
  printState.forEach(state => {{ state.item.hidden = false; if (state.item.tagName === 'DETAILS') state.item.open = true; }});
}});
window.addEventListener('afterprint', () => printState.forEach(state => {{ state.item.hidden = state.hidden; if (state.item.tagName === 'DETAILS') state.item.open = state.open; }}));
</script></body></html>"""
    markdown.extend(["## Quarterly history", "", "| Period | Revenue | CFO | Capex | FCF |", "|---|---:|---:|---:|---:|"])
    for row in quarter_rows:
        markdown.append("| " + " | ".join(str(row.get(key) if row.get(key) is not None else "Not available") for key in ("period", "revenue", "operating_cash_flow", "capex", "free_cash_flow")) + " |")
    markdown.extend(["", "## Evidence sources", ""])
    markdown.extend(f"- {source_id}: {source['url']} | SHA-256 `{source['sha256']}`" for source_id, source in sources.items())
    markdown.extend(["", "## Professional review", "", f"Status: {review['status']} | Review: `{review['id']}`", f"Digest: `{governance['release_digest']}`", "", "## Coverage limits", ""])
    markdown.extend(f"- {text}" for text in data["coverage"]["limitations"])
    markdown.extend(f"- {item['section']}.{item['metric']}: {item['reason']}" for item in data["coverage"]["missing_metrics"])
    output.mkdir(parents=True, exist_ok=True)
    path = output / "financial_report.html"
    path.write_text(document, encoding="utf-8")
    (output / "financial_report.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    return path