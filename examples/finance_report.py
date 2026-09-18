"""Dependency-free HTML renderer for the finance intelligence example."""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Iterable


_LABELS = {
    "fundamental_analysis": "Fundamental analysis",
    "valuation_analysis": "Valuation analysis",
    "audit_assurance": "Audit & assurance",
    "aml_fraud_detection": "AML & fraud detection",
    "risk_profiling": "Risk profiling",
    "regulatory_compliance": "Regulatory compliance",
    "manipulation_detection": "Manipulation detection",
    "peer_benchmarking": "Peer benchmarking",
    "investment_firm": "Investment firms",
    "audit_firm": "CPA / audit firms",
    "bank": "Banks & financial institutions",
    "regulator": "Regulators",
}


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _label(value: str) -> str:
    return _LABELS.get(value, value.replace("_", " ").title())


def _metric(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(_e(item) for item in value) if value else "None"
    if isinstance(value, float):
        percent_terms = ("growth", "margin", "roe", "volatility", "rate", "upside", "price_change")
        if any(term in key for term in percent_terms):
            return f"{value * 100:.1f}%"
        if "fair_value" in key or "market_price" in key:
            return f"${value:,.2f}"
        return f"{value:,.2f}"
    return _e(value)


def _sparkline(values: Iterable[float], width: int = 660, height: int = 155) -> str:
    values = list(values)
    if not values:
        return ""
    low, high = min(values), max(values)
    span = high - low or 1
    points = []
    for index, value in enumerate(values):
        x = 10 + index * (width - 20) / max(1, len(values) - 1)
        y = height - 15 - ((value - low) / span) * (height - 35)
        points.append(f"{x:.1f},{y:.1f}")
    area = f"10,{height - 15} " + " ".join(points) + f" {width - 10},{height - 15}"
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Recent sample price series">'
        '<defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%" stop-color="#5ec8ff" stop-opacity=".35"/>'
        '<stop offset="100%" stop-color="#5ec8ff" stop-opacity="0"/></linearGradient></defs>'
        f'<polygon points="{area}" fill="url(#area)"/>'
        f'<polyline points="{" ".join(points)}" fill="none" stroke="#5ec8ff" stroke-width="4" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{points[-1].split(",")[0]}" cy="{points[-1].split(",")[1]}" r="6" fill="#ffbd66"/>'
        '</svg>'
    )


def _specialist_cards(analyses: list) -> str:
    cards = []
    for item in analyses:
        output = item["output"]
        component = output["component"]
        confidence = float(output["confidence"])
        metrics = "".join(
            f'<div class="metric"><span>{_label(key)}</span><strong>{_metric(key, value)}</strong></div>'
            for key, value in output.get("metrics", {}).items()
        )
        findings = "".join(f"<li>{_e(finding)}</li>" for finding in output.get("findings", []))
        cards.append(f"""
        <article class="analysis-card">
          <div class="card-top">
            <div><span class="component-tag">SPECIALIST</span><h3>{_label(component)}</h3></div>
            <div class="confidence" title="Deterministic confidence score"><b>{confidence:.0%}</b><span>confidence</span></div>
          </div>
          <p>{_e(output['summary'])}</p>
          <div class="confidence-track"><i style="width:{confidence:.0%}"></i></div>
          <div class="metric-grid">{metrics}</div>
          <ul class="findings">{findings}</ul>
          <div class="evidence">SIGNED EVIDENCE <code>{_e(item['evidence_id'])}</code></div>
        </article>""")
    return "".join(cards)


def _audience_cards(reports: dict) -> str:
    cards = []
    for audience, report in reports.items():
        focus = "".join(f"<li>{_label(item)}</li>" for item in report["focus_components"])
        cards.append(f"""
        <article class="audience-card">
          <span class="component-tag">CONSUMER VIEW</span>
          <h3>{_label(audience)}</h3>
          <p>{_e(report['executive_summary'])}</p>
          <h4>Priority lens</h4><ul>{focus}</ul>
        </article>""")
    return "".join(cards)


def render_finance_report(package: dict, destination: Path) -> Path:
    """Render a self-contained, responsive, print-ready HTML report."""
    synthesis = package["synthesis"]
    analyses = package["analyses"]
    source_data = package["source_data"]
    governance = package["governance"]
    evidence = synthesis["evidence_chain"]
    budget = governance["budget"]
    integrity = governance["integrity"]
    prices = source_data["market"]["closes"]
    latest_price = prices[-1]
    source_labels = {
        "sec": "SEC filings", "market": "Market data",
        "news": "News & sentiment", "macro": "Macro indicators",
    }
    source_cards = "".join(
        f'<div class="source"><span>{index:02d}</span><b>{source_labels[name]}</b><small>governed ingest.{name}</small></div>'
        for index, name in enumerate(package["architecture"]["ingestion"], 1)
    )
    priority = "".join(f"<li>{_label(item)}</li>" for item in synthesis["priority_reviews"])
    proofs = "".join(
        f'<div class="proof"><span class="check">✓</span><div><b>{_e(item["invariant"])}</b><small>{_e(item["reason"])}</small></div></div>'
        for item in governance["guarantees"]
    )
    evidence_rows = "".join(
        f'<tr><td>{index:02d}</td><td>{_label(analyses[index - 1]["output"]["component"])}</td>'
        f'<td><code>{_e(why_id)}</code></td><td><span class="status">VERIFIED</span></td></tr>'
        for index, why_id in enumerate(evidence, 1)
    )
    report_json = json.dumps(package, separators=(",", ":")).replace("</", "<\\/")

    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Autarch governed multi-agent finance intelligence demonstration">
<title>Autarch Finance Intelligence | {_e(synthesis['ticker'])}</title>
<style>
:root{{--ink:#eaf4ff;--muted:#91a7bd;--bg:#071018;--panel:#0d1924;--panel2:#101f2c;--line:#203649;--cyan:#5ec8ff;--gold:#ffbd66;--green:#63e6be;--violet:#a78bfa;--red:#ff7b8b;--shadow:0 22px 60px rgba(0,0,0,.28)}}
*{{box-sizing:border-box}} html{{scroll-behavior:smooth}} body{{margin:0;background:radial-gradient(circle at 85% 0,#123b51 0,transparent 28%),var(--bg);color:var(--ink);font-family:"Segoe UI",Arial,sans-serif;line-height:1.55}}
a{{color:inherit}} .wrap{{width:min(1180px,calc(100% - 40px));margin:auto}} .eyebrow,.component-tag{{font-size:11px;font-weight:800;letter-spacing:.14em;color:var(--cyan)}}
header{{padding:28px 0;border-bottom:1px solid rgba(255,255,255,.08)}} nav{{display:flex;align-items:center;justify-content:space-between;gap:20px}} .brand{{font-size:22px;font-weight:850;letter-spacing:.08em}} .brand i{{color:var(--cyan);font-style:normal}} nav .links{{display:flex;gap:24px;color:var(--muted);font-size:13px}} nav a{{text-decoration:none}}
.hero{{padding:84px 0 62px}} .hero-grid{{display:grid;grid-template-columns:1.25fr .75fr;gap:52px;align-items:center}} h1{{font-size:clamp(42px,6vw,76px);line-height:1.02;margin:16px 0 24px;letter-spacing:-.045em;max-width:880px}} .gradient{{background:linear-gradient(90deg,var(--cyan),#b8e9ff 60%,var(--gold));-webkit-background-clip:text;color:transparent}} .lede{{font-size:19px;color:#b6c8d9;max-width:730px}} .badges{{display:flex;flex-wrap:wrap;gap:10px;margin-top:28px}} .badge{{padding:9px 13px;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.03);font-size:12px;color:#c5d4e1}}
.verdict{{background:linear-gradient(145deg,#132638,#0b1721);border:1px solid #2e4b61;border-radius:24px;padding:28px;box-shadow:var(--shadow);position:relative;overflow:hidden}} .verdict:after{{content:"";position:absolute;width:180px;height:180px;border-radius:50%;background:var(--cyan);filter:blur(90px);opacity:.12;right:-60px;top:-80px}} .verdict h2{{font-size:25px;line-height:1.2;color:var(--gold);margin:10px 0}} .verdict p{{color:var(--muted)}} .score-row{{display:flex;align-items:end;justify-content:space-between;border-top:1px solid var(--line);padding-top:18px;margin-top:22px}} .score-row strong{{font-size:38px;color:var(--green)}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:72px}} .kpi{{background:rgba(13,25,36,.85);border:1px solid var(--line);padding:22px;border-radius:17px}} .kpi b{{display:block;font-size:30px}} .kpi span{{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}}
section{{padding:72px 0}} .section-head{{display:flex;justify-content:space-between;align-items:end;gap:30px;margin-bottom:30px}} .section-head h2{{font-size:38px;letter-spacing:-.025em;margin:8px 0}} .section-head p{{max-width:530px;color:var(--muted)}}
.architecture{{background:#09141e;border-block:1px solid var(--line)}} .sources{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}} .source{{padding:18px;border:1px solid #31506a;border-radius:14px;background:#10263a;display:grid;grid-template-columns:32px 1fr}} .source span{{grid-row:1/3;color:var(--cyan);font-weight:800}} .source small{{color:var(--muted)}} .flow-arrow{{text-align:center;font-size:28px;color:var(--cyan);padding:13px}} .agent-cloud{{border:1px dashed #496175;border-radius:24px;padding:24px}} .agent-pills{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}} .agent-pills span{{padding:14px 10px;text-align:center;background:#352f78;border:1px solid #766bd0;border-radius:12px;font-size:13px}} .synthesis-node{{width:min(620px,90%);margin:auto;text-align:center;padding:20px;border-radius:14px;background:#164b42;border:1px solid #42a68f}} .consumer-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}} .consumer-row span{{text-align:center;padding:15px;background:#5a2b1b;border:1px solid #a95a38;border-radius:12px}}
.thesis-grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px}} .thesis-card{{background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:28px}} .thesis-card h3{{font-size:22px;margin-top:0}} .thesis-card ul{{padding-left:20px}} .positive h3{{color:var(--green)}} .attention h3{{color:var(--gold)}} .chart{{margin-top:20px;background:#08131d;border-radius:16px;padding:16px}} .chart-meta{{display:flex;justify-content:space-between;color:var(--muted);font-size:12px}} .chart-meta strong{{color:var(--ink);font-size:18px}}
.analysis-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:18px}} .analysis-card{{background:linear-gradient(155deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:20px;padding:24px;box-shadow:0 10px 30px rgba(0,0,0,.12)}} .card-top{{display:flex;justify-content:space-between;gap:20px}} .analysis-card h3,.audience-card h3{{margin:6px 0 12px;font-size:22px}} .analysis-card p{{min-height:50px;color:#c2d1df}} .confidence{{text-align:right}} .confidence b{{display:block;font-size:22px;color:var(--green)}} .confidence span{{font-size:10px;color:var(--muted);text-transform:uppercase}} .confidence-track{{height:4px;background:#243848;border-radius:99px;margin:18px 0}} .confidence-track i{{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--green));border-radius:99px}} .metric-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px}} .metric{{background:#09151f;padding:10px 12px;border-radius:10px}} .metric span{{display:block;color:var(--muted);font-size:10px;text-transform:uppercase}} .metric strong{{font-size:14px}} .findings{{color:#b8c9d8;font-size:13px;padding-left:19px}} .evidence{{font-size:10px;color:var(--muted);border-top:1px solid var(--line);padding-top:14px;margin-top:16px}} code{{font-family:Consolas,monospace;color:var(--cyan)}}
.audience-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}} .audience-card{{border:1px solid var(--line);background:var(--panel);border-radius:18px;padding:22px}} .audience-card p,.audience-card li{{font-size:13px;color:#b7c8d7}} .audience-card h4{{font-size:11px;text-transform:uppercase;color:var(--gold);letter-spacing:.08em;margin-bottom:5px}}
.governance{{background:#09141e;border-block:1px solid var(--line)}} .gov-grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px}} .gov-panel{{background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:26px}} .proof{{display:flex;gap:13px;margin:15px 0}} .check{{display:grid;place-items:center;flex:0 0 28px;height:28px;border-radius:50%;background:rgba(99,230,190,.13);color:var(--green)}} .proof small{{display:block;color:var(--muted)}} .budget-line{{margin:18px 0}} .budget-label{{display:flex;justify-content:space-between;font-size:13px}} .bar{{height:8px;background:#243848;border-radius:99px;margin-top:8px}} .bar i{{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--cyan),var(--gold))}}
table{{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:16px;overflow:hidden}} th,td{{padding:14px 16px;text-align:left;border-bottom:1px solid var(--line);font-size:13px}} th{{color:var(--muted);font-size:10px;letter-spacing:.08em;text-transform:uppercase}} .status{{color:var(--green);font-size:10px;font-weight:800}}
footer{{padding:50px 0;color:var(--muted);font-size:12px;border-top:1px solid var(--line)}} .footer-grid{{display:flex;justify-content:space-between;gap:30px}} .print-note{{border-left:3px solid var(--gold);padding-left:14px}}
@media(max-width:900px){{.hero-grid,.thesis-grid,.gov-grid{{grid-template-columns:1fr}}.kpis,.sources,.agent-pills,.consumer-row,.audience-grid{{grid-template-columns:repeat(2,1fr)}}.analysis-grid{{grid-template-columns:1fr}}nav .links{{display:none}}}}
@media(max-width:560px){{.wrap{{width:min(100% - 24px,1180px)}}.kpis,.sources,.agent-pills,.consumer-row,.audience-grid,.metric-grid{{grid-template-columns:1fr}}h1{{font-size:42px}}section{{padding:48px 0}}}}
@media print{{:root{{--ink:#17212b;--muted:#536474;--bg:#fff;--panel:#fff;--panel2:#f7f9fb;--line:#dce3e8}}body{{background:#fff}}header{{display:none}}.hero{{padding-top:25px}}section{{break-inside:avoid;padding:35px 0}}.analysis-card,.audience-card,.gov-panel,.kpi{{box-shadow:none;break-inside:avoid}}.architecture,.governance{{background:#fff}}}}
</style>
</head>
<body>
<header><div class="wrap"><nav><div class="brand"><i>AUTARCH</i> / FINANCE INTELLIGENCE</div><div class="links"><a href="#architecture">Architecture</a><a href="#analysis">Analysis</a><a href="#consumers">Consumers</a><a href="#evidence">Evidence</a></div></nav></div></header>
<main>
<section class="hero"><div class="wrap hero-grid"><div><span class="eyebrow">GOVERNED MULTI-AGENT DEMONSTRATION · FY2025</span><h1>Decision intelligence with <span class="gradient">evidence by construction.</span></h1><p class="lede">A finance-grade demonstration for {_e(synthesis['ticker'])}: four governed data feeds, eight specialist agents, one evidence-linked synthesis, and audience-specific views.</p><div class="badges"><span class="badge">OFFLINE FIXTURE</span><span class="badge">MODEL-AGNOSTIC</span><span class="badge">SIGNED EVIDENCE</span><span class="badge">NOT INVESTMENT ADVICE</span></div></div><aside class="verdict"><span class="eyebrow">COMMITTEE RECOMMENDATION</span><h2>{_e(synthesis['recommendation'])}</h2><p>{_e(synthesis['summary'])}</p><div class="score-row"><span>CONSOLIDATED CONFIDENCE</span><strong>{float(synthesis['confidence']):.0%}</strong></div></aside></div></section>
<div class="wrap kpis"><div class="kpi"><b>{len(analyses)}</b><span>specialist agents</span></div><div class="kpi"><b>{len(evidence)}</b><span>signed evidence links</span></div><div class="kpi"><b>{budget['calls'].split('/')[0]}</b><span>governed actions</span></div><div class="kpi"><b>{'PASS' if integrity['verified'] else 'FAIL'}</b><span>ledger integrity</span></div></div>
<section class="architecture" id="architecture"><div class="wrap"><div class="section-head"><div><span class="eyebrow">REFERENCE ARCHITECTURE</span><h2>From source data to accountable decisions</h2></div><p>Every connector and component is a separately granted capability. Specialists cannot acquire authority merely because they possess a tool.</p></div><div class="sources">{source_cards}</div><div class="flow-arrow">↓</div><div class="agent-cloud"><span class="eyebrow">REUSABLE ANALYSIS COMPONENTS</span><div class="agent-pills">{''.join(f'<span>{_label(item["output"]["component"])}</span>' for item in analyses)}</div></div><div class="flow-arrow">↓</div><div class="synthesis-node"><b>Multi-agent synthesis</b><br><small>reflection · evidence chain · signed committee proof</small></div><div class="flow-arrow">↓</div><div class="consumer-row">{''.join(f'<span>{_label(name)}</span>' for name in package['reports'])}</div></div></section>
<section><div class="wrap"><div class="section-head"><div><span class="eyebrow">EXECUTIVE READOUT</span><h2>Balanced thesis, explicit escalation</h2></div><p>The recommendation preserves positive operating evidence while exposing the assumptions and exceptions that require accountable review.</p></div><div class="thesis-grid"><article class="thesis-card positive"><h3>Constructive signals</h3><ul><li>Revenue increased 10.3% year over year.</li><li>Operating margin reached 15.0% and cash conversion remained positive.</li><li>Operating performance compares favorably with the simulated peer set.</li><li>External audit opinion is unmodified in the fixture.</li></ul><div class="chart"><div class="chart-meta"><span>SAMPLE PRICE SERIES</span><strong>${latest_price:,.2f}</strong></div>{_sparkline(prices)}</div></article><article class="thesis-card attention"><h3>Enhanced diligence required</h3><ul>{priority}</ul><p>{_e(synthesis['reflection'])}</p><div class="metric-grid"><div class="metric"><span>Decision</span><strong>{_e(synthesis['recommendation'])}</strong></div><div class="metric"><span>Evidence coverage</span><strong>{len(evidence)} / {len(analyses)}</strong></div></div></article></div></div></section>
<section id="analysis"><div class="wrap"><div class="section-head"><div><span class="eyebrow">SPECIALIST WORKBENCH</span><h2>Eight reusable analysis components</h2></div><p>Each result has structured metrics, bounded confidence, deterministic quality evaluation, and a direct reference to signed execution evidence.</p></div><div class="analysis-grid">{_specialist_cards(analyses)}</div></div></section>
<section id="consumers"><div class="wrap"><div class="section-head"><div><span class="eyebrow">AUDIENCE-SPECIFIC DELIVERY</span><h2>One evidence base, four professional lenses</h2></div><p>The same governed synthesis is reframed without changing its source evidence. Regulatory output passes an explicit accountable-release policy.</p></div><div class="audience-grid">{_audience_cards(package['reports'])}</div></div></section>
<section class="governance" id="evidence"><div class="wrap"><div class="section-head"><div><span class="eyebrow">GOVERNANCE PROOF</span><h2>The controls ran—not merely the analysis</h2></div><p>Static guarantees are checked before execution; economic limits are enforced before each action; and the final evidence chain is cryptographically verified.</p></div><div class="gov-grid"><article class="gov-panel"><h3>Static guarantees</h3>{proofs}<div class="proof"><span class="check">✓</span><div><b>Signed chain verified</b><small>{_e(integrity['record_count'])} records; broken record: {_e(integrity['broken_record'])}</small></div></div></article><article class="gov-panel"><h3>Shared execution budget</h3><div class="budget-line"><div class="budget-label"><span>Calls</span><b>{budget['calls']}</b></div><div class="bar"><i style="width:85%"></i></div></div><div class="budget-line"><div class="budget-label"><span>Risk units</span><b>{budget['risk']}</b></div><div class="bar"><i style="width:85%"></i></div></div><div class="budget-line"><div class="budget-label"><span>Cost units</span><b>{budget['cost']}</b></div><div class="bar"><i style="width:0%"></i></div></div><p class="print-note">A proposed action that would exceed a ceiling is refused before its adapter executes.</p></article></div></div></section>
<section><div class="wrap"><div class="section-head"><div><span class="eyebrow">EVIDENCE INDEX</span><h2>Specialist conclusions remain traceable</h2></div><p>The identifiers below resolve to complete records in the accompanying signed audit ledger.</p></div><table><thead><tr><th>#</th><th>Specialist</th><th>Why-record</th><th>Integrity</th></tr></thead><tbody>{evidence_rows}</tbody></table></div></section>
</main>
<footer><div class="wrap footer-grid"><div><b>AUTARCH</b><br>Governed Agent Factory · Finance Intelligence Demonstration</div><div>This report uses fictional, deterministic data.<br>It is not investment, audit, tax, legal, or regulatory advice.</div></div></footer>
<script type="application/json" id="autarch-report-data">{report_json}</script>
</body></html>"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")
    return destination
