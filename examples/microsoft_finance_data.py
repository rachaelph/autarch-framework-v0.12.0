"""Live, cached public-data connectors for Microsoft Finance Intelligence.

The module intentionally uses only the Python standard library. Every response is
stored in a content-addressed cache with its URL, retrieval time, SHA-256 digest,
and freshness status. Public websites change; normalizers therefore fail with an
explicit error instead of silently inventing a value.
"""
from __future__ import annotations

import csv
import calendar
import hashlib
import html
import io
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SEC_CIK = "0000789019"
SEC_CIK_UNPADDED = "789019"
TICKER = "MSFT"
DEFAULT_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "AutarchFinanceIntelligence/0.12 research@example.com",
)


@dataclass(frozen=True)
class SourceResponse:
    """A fetched or cached source body and its immutable metadata."""

    source_id: str
    body: bytes
    metadata: Dict[str, Any]


class SourceCache:
    """Content-addressed HTTP cache supporting live, hybrid, and cache-only runs."""

    def __init__(
        self,
        root: Path,
        *,
        mode: str = "hybrid",
        user_agent: str = DEFAULT_USER_AGENT,
        ttl_hours: float = 12.0,
        timeout: float = 90.0,
    ) -> None:
        if mode not in {"live", "hybrid", "cache-only"}:
            raise ValueError("mode must be live, hybrid, or cache-only")
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.index_path = self.root / "index.json"
        self.mode = mode
        self.user_agent = user_agent
        self.ttl_hours = float(ttl_hours)
        self.timeout = float(timeout)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._index = self._load_index()
        self._used: Dict[str, Dict[str, Any]] = {}

    def _load_index(self) -> Dict[str, Dict[str, Any]]:
        if not self.index_path.exists():
            return {}
        try:
            value = json.loads(self.index_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_index(self) -> None:
        temporary = self.index_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._index, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.index_path)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _cached(self, source_id: str) -> Optional[SourceResponse]:
        entry = self._index.get(source_id)
        if not entry:
            return None
        path = self.root / entry.get("cache_path", "")
        if not path.is_file():
            return None
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != entry.get("sha256"):
            return None
        metadata = dict(entry)
        metadata["accessed_at"] = self._now().isoformat()
        return SourceResponse(source_id, body, metadata)

    @staticmethod
    def _is_fresh(response: SourceResponse, ttl_hours: float) -> bool:
        try:
            retrieved = datetime.fromisoformat(response.metadata["retrieved_at"])
        except (KeyError, TypeError, ValueError):
            return False
        if retrieved.tzinfo is None:
            retrieved = retrieved.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - retrieved <= timedelta(hours=ttl_hours)

    def fetch(
        self,
        source_id: str,
        url: str,
        *,
        suffix: str,
        provider: str,
        authoritative: bool,
        description: str,
        evidence_class: Optional[str] = None,
        ttl_hours: Optional[float] = None,
        extra_headers: Optional[Mapping[str, str]] = None,
    ) -> SourceResponse:
        """Fetch a URL according to cache mode and register the evidence metadata."""
        ttl = self.ttl_hours if ttl_hours is None else float(ttl_hours)
        cached = self._cached(source_id)
        declared_metadata = {
            "provider": provider,
            "description": description,
            "authoritative": bool(authoritative),
            "evidence_class": evidence_class or ("authoritative public source" if authoritative else "indicative public source"),
            "url": url,
        }
        cached_url_matches = cached is not None and cached.metadata.get("url") == url
        if self.mode == "cache-only":
            if cached is None or not cached_url_matches:
                reason = "no valid entry" if cached is None else "cached URL does not match the requested source"
                raise RuntimeError(f"cache-only mode has {reason} for {source_id}")
            metadata = dict(cached.metadata, **declared_metadata, retrieval_mode="cache", freshness="cached")
            response = SourceResponse(source_id, cached.body, metadata)
            self._used[source_id] = metadata
            return response
        if self.mode == "hybrid" and cached_url_matches and self._is_fresh(cached, ttl):
            metadata = dict(cached.metadata, **declared_metadata, retrieval_mode="cache", freshness="fresh")
            response = SourceResponse(source_id, cached.body, metadata)
            self._used[source_id] = metadata
            return response

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json,text/csv,text/html,application/xhtml+xml,*/*",
            "Accept-Encoding": "identity",
        }
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as remote:
                body = remote.read()
                content_type = remote.headers.get("Content-Type", "application/octet-stream")
                status = int(remote.status)
                etag = remote.headers.get("ETag")
                last_modified = remote.headers.get("Last-Modified")
        except (OSError, urllib.error.URLError) as exc:
            if self.mode == "hybrid" and cached_url_matches:
                metadata = dict(
                    cached.metadata,
                    **declared_metadata,
                    retrieval_mode="cache-fallback",
                    freshness="stale",
                    fallback_reason=f"{type(exc).__name__}: {exc}",
                )
                response = SourceResponse(source_id, cached.body, metadata)
                self._used[source_id] = metadata
                return response
            raise RuntimeError(f"failed to retrieve {source_id} from {url}: {exc}") from exc

        digest = hashlib.sha256(body).hexdigest()
        safe_id = re.sub(r"[^a-z0-9]+", "-", source_id.lower()).strip("-")
        extension = suffix.lstrip(".")
        relative_path = Path("raw") / f"{safe_id}--{digest[:16]}.{extension}"
        path = self.root / relative_path
        if not path.exists():
            path.write_bytes(body)
        now = self._now().isoformat()
        metadata = {
            "source_id": source_id,
            "provider": provider,
            "description": description,
            "authoritative": bool(authoritative),
            "evidence_class": declared_metadata["evidence_class"],
            "url": url,
            "retrieved_at": now,
            "accessed_at": now,
            "sha256": digest,
            "bytes": len(body),
            "content_type": content_type,
            "http_status": status,
            "etag": etag,
            "last_modified": last_modified,
            "cache_path": relative_path.as_posix(),
            "retrieval_mode": "live",
            "freshness": "fresh",
        }
        self._index[source_id] = {key: value for key, value in metadata.items()
                                  if key not in {"retrieval_mode", "freshness", "accessed_at"}}
        self._write_index()
        self._used[source_id] = metadata
        return SourceResponse(source_id, body, metadata)

    def fetch_json(self, *args: Any, **kwargs: Any) -> Tuple[Any, Dict[str, Any]]:
        response = self.fetch(*args, suffix="json", **kwargs)
        try:
            return json.loads(response.body), response.metadata
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError(f"{response.source_id} did not contain valid JSON") from exc

    def fetch_text(self, *args: Any, **kwargs: Any) -> Tuple[str, Dict[str, Any]]:
        response = self.fetch(*args, suffix="html", **kwargs)
        return response.body.decode("utf-8", errors="replace"), response.metadata

    def fetch_csv(self, *args: Any, **kwargs: Any) -> Tuple[str, Dict[str, Any]]:
        response = self.fetch(*args, suffix="csv", **kwargs)
        return response.body.decode("utf-8-sig", errors="replace"), response.metadata

    def manifest(self) -> Dict[str, Dict[str, Any]]:
        """Return metadata for exactly the sources used by this run."""
        return {key: dict(value) for key, value in sorted(self._used.items())}


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() in {"script", "style", "svg"}:
            self._ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "svg"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored and data.strip():
            self.parts.append(data.strip())


def visible_text(document: str) -> str:
    parser = _VisibleText()
    parser.feed(document)
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def _tags(company_facts: Mapping[str, Any], names: Sequence[str]) -> List[Tuple[int, str, Mapping[str, Any]]]:
    namespaces = company_facts.get("facts", {})
    matches: List[Tuple[int, str, Mapping[str, Any]]] = []
    for namespace in ("us-gaap", "dei"):
        facts = namespaces.get(namespace, {})
        for priority, name in enumerate(names):
            if name in facts:
                matches.append((priority, name, facts[name]))
    return matches


def _duration_is_annual(item: Mapping[str, Any]) -> bool:
    try:
        start = date.fromisoformat(str(item["start"]))
        end = date.fromisoformat(str(item["end"]))
    except (KeyError, TypeError, ValueError):
        return False
    return 300 <= (end - start).days <= 400


def _unit_rows(fact: Mapping[str, Any], preferred_units: Sequence[str]) -> Tuple[List[dict], str]:
    units = fact.get("units", {})
    for unit in preferred_units:
        if unit in units:
            return list(units[unit]), unit
    if units:
        unit = next(iter(units))
        return list(units[unit]), unit
    return [], ""


def _tag_matches(
    company_facts: Mapping[str, Any], names: Sequence[str]
) -> List[Tuple[int, str, str, Mapping[str, Any]]]:
    """Return matching concepts with namespace and deterministic alias priority."""
    namespaces = company_facts.get("facts", {})
    matches: List[Tuple[int, str, str, Mapping[str, Any]]] = []
    for namespace in ("us-gaap", "dei"):
        facts = namespaces.get(namespace, {})
        for priority, name in enumerate(names):
            if name in facts:
                matches.append((priority, namespace, name, facts[name]))
    return matches


def _normalized_unit(unit: str, divisor: float) -> str:
    if divisor == 1_000_000_000.0:
        return f"{unit} billions"
    if divisor == 1_000_000.0:
        return f"{unit} millions"
    return unit


def _stable_fact_id(identity: Mapping[str, Any]) -> str:
    """Return a full-width content identity for an extracted or derived fact."""
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return "fact_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fact_record(
    metric: str,
    period: str,
    namespace: str,
    concept: str,
    unit: str,
    item: Mapping[str, Any],
    divisor: float,
    *,
    selection_method: str,
) -> Optional[Dict[str, Any]]:
    try:
        raw_value = float(item["val"])
        value = round(raw_value / divisor, 6)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    identity = {
        "metric": metric,
        "period": period,
        "namespace": namespace,
        "concept": concept,
        "unit": unit,
        "start": item.get("start"),
        "end": item.get("end"),
        "accession": item.get("accn"),
        "raw_value": raw_value,
    }
    fact_id = _stable_fact_id(identity)
    return {
        "fact_id": fact_id,
        "metric": metric,
        "period": period,
        "namespace": namespace,
        "concept": concept,
        "unit": unit,
        "normalized_unit": _normalized_unit(unit, divisor),
        "raw_value": raw_value,
        "value": value,
        "divisor": divisor,
        "start": item.get("start"),
        "end": item.get("end"),
        "form": item.get("form"),
        "fiscal_year_reported": item.get("fy"),
        "fiscal_period_reported": item.get("fp"),
        "filed": item.get("filed"),
        "accession": item.get("accn"),
        "frame": item.get("frame"),
        "source_id": "S-SEC-COMPANYFACTS",
        "selection_method": selection_method,
        "derived": False,
    }


def _candidate_rank(item: Mapping[str, Any]) -> Tuple[str, str, int]:
    return (
        str(item.get("filed", "")),
        str(item.get("accn", "")),
        -int(item.get("_priority", 999)),
    )


def _selected_fact(
    metric: str,
    period: str,
    candidates: Sequence[Mapping[str, Any]],
    divisor: float,
    *,
    selection_method: str,
) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None
    selected = max(candidates, key=_candidate_rank)
    record = _fact_record(
        metric,
        period,
        str(selected.get("_namespace", "")),
        str(selected.get("_concept", "")),
        str(selected.get("_unit", "")),
        selected,
        divisor,
        selection_method=selection_method,
    )
    if record is None:
        return None
    chain = []
    for candidate in sorted(candidates, key=_candidate_rank):
        candidate_record = _fact_record(
            metric,
            period,
            str(candidate.get("_namespace", "")),
            str(candidate.get("_concept", "")),
            str(candidate.get("_unit", "")),
            candidate,
            divisor,
            selection_method=selection_method,
        )
        if candidate_record is None:
            continue
        chain.append({
            "fact_id": candidate_record["fact_id"],
            "concept": candidate_record["concept"],
            "filed": candidate_record["filed"],
            "accession": candidate_record["accession"],
            "form": candidate_record["form"],
            "raw_value": candidate_record["raw_value"],
            "value": candidate_record["value"],
            "selected": candidate_record["fact_id"] == record["fact_id"],
        })
    distinct_values = {entry["raw_value"] for entry in chain}
    record["recast_chain"] = chain
    record["recast_observation_count"] = len(chain)
    record["recast_value_count"] = len(distinct_values)
    record["value_changed_in_recast_chain"] = len(distinct_values) > 1
    return record


def annual_series_with_lineage(
    company_facts: Mapping[str, Any],
    metric: str,
    names: Sequence[str],
    *,
    preferred_units: Sequence[str] = ("USD",),
    divisor: float = 1_000_000_000.0,
    years: int = 5,
    forms: Sequence[str] = ("10-K",),
) -> Dict[str, Dict[str, Any]]:
    """Select annual values and retain exact source/recast metadata."""
    candidates: Dict[str, List[Dict[str, Any]]] = {}
    for priority, namespace, concept, fact in _tag_matches(company_facts, names):
        rows, unit = _unit_rows(fact, preferred_units)
        for source_item in rows:
            if source_item.get("form") not in forms or source_item.get("fp") != "FY" or not _duration_is_annual(source_item):
                continue
            end = str(source_item.get("end", ""))
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
                continue
            candidates.setdefault(end, []).append(dict(
                source_item,
                _priority=priority,
                _namespace=namespace,
                _concept=concept,
                _unit=unit,
            ))
    result: Dict[str, Dict[str, Any]] = {}
    for end in sorted(candidates)[-years:]:
        period = f"FY{end[:4]}"
        record = _selected_fact(
            metric, period, candidates[end], divisor, selection_method="latest_annual_recast"
        )
        if record is not None:
            result[end[:4]] = record
    return result


def instant_series_with_lineage(
    company_facts: Mapping[str, Any],
    metric: str,
    names: Sequence[str],
    *,
    preferred_units: Sequence[str] = ("USD",),
    divisor: float = 1_000_000_000.0,
    years: int = 3,
    year_end_dates: Optional[Sequence[str]] = None,
    forms: Sequence[str] = ("10-K",),
) -> Dict[str, Dict[str, Any]]:
    """Select year-end instant values and retain exact source/recast metadata."""
    is_microsoft = str(company_facts.get("cik", "")).lstrip("0") == SEC_CIK_UNPADDED
    candidates: Dict[str, List[Dict[str, Any]]] = {}
    for priority, namespace, concept, fact in _tag_matches(company_facts, names):
        rows, unit = _unit_rows(fact, preferred_units)
        for source_item in rows:
            if source_item.get("form") not in forms or source_item.get("start"):
                continue
            end = str(source_item.get("end", ""))
            if year_end_dates is not None and end not in year_end_dates:
                continue
            if year_end_dates is None and is_microsoft and not end.endswith("-06-30"):
                continue
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
                continue
            candidates.setdefault(end, []).append(dict(
                source_item,
                _priority=priority,
                _namespace=namespace,
                _concept=concept,
                _unit=unit,
            ))
    result: Dict[str, Dict[str, Any]] = {}
    for end in sorted(candidates)[-years:]:
        period = f"FY{end[:4]} year-end"
        record = _selected_fact(
            metric, period, candidates[end], divisor, selection_method="latest_year_end_recast"
        )
        if record is not None:
            result[end[:4]] = record
    return result


def annual_series(
    company_facts: Mapping[str, Any],
    names: Sequence[str],
    *,
    preferred_units: Sequence[str] = ("USD",),
    divisor: float = 1_000_000_000.0,
    years: int = 5,
) -> Dict[str, float]:
    """Select one annual 10-K fact per fiscal end, preferring the newest recast."""
    facts = _tags(company_facts, names)
    if not facts:
        return {}
    selected: Dict[str, dict] = {}
    for priority, concept, fact in facts:
        rows, _unit = _unit_rows(fact, preferred_units)
        for source_item in rows:
            if source_item.get("form") != "10-K" or source_item.get("fp") != "FY" or not _duration_is_annual(source_item):
                continue
            end = str(source_item.get("end", ""))
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
                continue
            item = dict(source_item, _concept=concept, _priority=priority)
            previous = selected.get(end)
            candidate_rank = (str(item.get("filed", "")), str(item.get("accn", "")), -priority)
            previous_rank = (
                str(previous.get("filed", "")), str(previous.get("accn", "")),
                -int(previous.get("_priority", 999)),
            ) if previous else None
            if previous is None or candidate_rank > previous_rank:
                selected[end] = item
    result: Dict[str, float] = {}
    for end, item in sorted(selected.items())[-years:]:
        try:
            result[end[:4]] = round(float(item["val"]) / divisor, 6)
        except (KeyError, TypeError, ValueError):
            continue
    return result


def instant_series(
    company_facts: Mapping[str, Any],
    names: Sequence[str],
    *,
    preferred_units: Sequence[str] = ("USD",),
    divisor: float = 1_000_000_000.0,
    years: int = 3,
) -> Dict[str, float]:
    """Select fiscal year-end balance-sheet facts from 10-K filings."""
    facts = _tags(company_facts, names)
    if not facts:
        return {}
    is_microsoft = str(company_facts.get("cik", "")).lstrip("0") == SEC_CIK_UNPADDED
    selected: Dict[str, dict] = {}
    for priority, concept, fact in facts:
        rows, _unit = _unit_rows(fact, preferred_units)
        for source_item in rows:
            if source_item.get("form") != "10-K" or source_item.get("start"):
                continue
            end = str(source_item.get("end", ""))
            if is_microsoft and not end.endswith("-06-30"):
                continue
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
                continue
            item = dict(source_item, _concept=concept, _priority=priority)
            previous = selected.get(end)
            candidate_rank = (str(item.get("filed", "")), str(item.get("accn", "")), -priority)
            previous_rank = (
                str(previous.get("filed", "")), str(previous.get("accn", "")),
                -int(previous.get("_priority", 999)),
            ) if previous else None
            if previous is None or candidate_rank > previous_rank:
                selected[end] = item
    result: Dict[str, float] = {}
    for end, item in sorted(selected.items())[-years:]:
        try:
            result[end[:4]] = round(float(item["val"]) / divisor, 6)
        except (KeyError, TypeError, ValueError):
            continue
    return result


ANNUAL_METRICS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...], float]] = {
    "revenue": (("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"), ("USD",), 1e9),
    "gross_profit": (("GrossProfit",), ("USD",), 1e9),
    "operating_income": (("OperatingIncomeLoss",), ("USD",), 1e9),
    "net_income": (("NetIncomeLoss", "ProfitLoss"), ("USD",), 1e9),
    "pretax_income": (("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"), ("USD",), 1e9),
    "income_tax": (("IncomeTaxExpenseBenefit",), ("USD",), 1e9),
    "operating_cash_flow": (("NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"), ("USD",), 1e9),
    "capex": (("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"), ("USD",), 1e9),
    "dividends_paid": (("PaymentsOfDividendsCommonStock", "PaymentsOfDividends"), ("USD",), 1e9),
    "share_repurchases": (("PaymentsForRepurchaseOfCommonStock",), ("USD",), 1e9),
    "stock_compensation": (("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"), ("USD",), 1e9),
    "research_development": (("ResearchAndDevelopmentExpense",), ("USD",), 1e9),
    "sales_marketing": (("SellingAndMarketingExpense",), ("USD",), 1e9),
    "general_administrative": (("GeneralAndAdministrativeExpense",), ("USD",), 1e9),
    "depreciation_amortization": (("DepreciationDepletionAndAmortization", "Depreciation", "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment"), ("USD",), 1e9),
    "cash_taxes_paid": (("IncomeTaxesPaidNet",), ("USD",), 1e9),
    "interest_expense": (("InterestExpenseNonoperating", "InterestExpenseNonOperating", "InterestExpense"), ("USD",), 1e9),
    "other_income_expense": (("NonoperatingIncomeExpense", "OtherNonoperatingIncomeExpense"), ("USD",), 1e9),
    "diluted_eps": (("EarningsPerShareDiluted",), ("USD/shares",), 1.0),
    "diluted_shares": (("WeightedAverageNumberOfDilutedSharesOutstanding",), ("shares",), 1e9),
}

BALANCE_METRICS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...], float]] = {
    "cash": (("CashAndCashEquivalentsAtCarryingValue",), ("USD",), 1e9),
    "short_term_investments": (("ShortTermInvestments", "MarketableSecuritiesCurrent"), ("USD",), 1e9),
    "accounts_receivable": (("AccountsReceivableNetCurrent",), ("USD",), 1e9),
    "current_assets": (("AssetsCurrent",), ("USD",), 1e9),
    "current_liabilities": (("LiabilitiesCurrent",), ("USD",), 1e9),
    "total_assets": (("Assets",), ("USD",), 1e9),
    "total_liabilities": (("Liabilities",), ("USD",), 1e9),
    "equity": (("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"), ("USD",), 1e9),
    "property_equipment": (("PropertyPlantAndEquipmentNet",), ("USD",), 1e9),
    "goodwill": (("Goodwill",), ("USD",), 1e9),
    "intangible_assets": (("FiniteLivedIntangibleAssetsNet", "IntangibleAssetsNetExcludingGoodwill"), ("USD",), 1e9),
    "deferred_revenue_current": (("DeferredRevenueCurrent", "ContractWithCustomerLiabilityCurrent"), ("USD",), 1e9),
    "deferred_revenue_noncurrent": (("DeferredRevenueNoncurrent", "ContractWithCustomerLiabilityNoncurrent"), ("USD",), 1e9),
    "long_term_debt_current": (("LongTermDebtCurrent",), ("USD",), 1e9),
    "long_term_debt_noncurrent": (("LongTermDebtNoncurrent",), ("USD",), 1e9),
    "finance_lease_current": (("FinanceLeaseLiabilityCurrent",), ("USD",), 1e9),
    "finance_lease_noncurrent": (("FinanceLeaseLiabilityNoncurrent",), ("USD",), 1e9),
    "finance_lease_total": (("FinanceLeaseLiability",), ("USD",), 1e9),
    "operating_lease_current": (("OperatingLeaseLiabilityCurrent",), ("USD",), 1e9),
    "operating_lease_noncurrent": (("OperatingLeaseLiabilityNoncurrent",), ("USD",), 1e9),
    "operating_lease_total": (("OperatingLeaseLiability",), ("USD",), 1e9),
    "equity_investments": (("EquitySecuritiesFvNi", "EquityMethodInvestments"), ("USD",), 1e9),
    "unrecognized_tax_benefits": (("UnrecognizedTaxBenefits",), ("USD",), 1e9),
    "shares_outstanding": (("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"), ("shares",), 1e9),
    "remaining_performance_obligation": (("RevenueRemainingPerformanceObligation",), ("USD",), 1e9),
}


QUARTERLY_METRICS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...], float]] = {
    key: ANNUAL_METRICS[key] for key in (
        "revenue",
        "gross_profit",
        "operating_income",
        "net_income",
        "operating_cash_flow",
        "capex",
        "research_development",
        "stock_compensation",
    )
}


def _fiscal_period(
    end: date, fiscal_year_end_month: int = 6, *, week_based: bool = False,
) -> Optional[Tuple[int, int]]:
    if not 1 <= fiscal_year_end_month <= 12:
        raise ValueError("fiscal_year_end_month must be between 1 and 12")
    if week_based:
        boundaries = [
            date(year, month, calendar.monthrange(year, month)[1])
            for year in (end.year - 1, end.year, end.year + 1)
            for month in range(1, 13)
            if (month - fiscal_year_end_month) % 3 == 0
        ]
        nearest = min(boundaries, key=lambda boundary: abs((end - boundary).days))
        if abs((end - nearest).days) > 7:
            return None
        end = nearest
    month_offset = (end.month - fiscal_year_end_month) % 12
    if month_offset % 3:
        return None
    quarter = month_offset // 3 or 4
    fiscal_year = end.year + 1 if end.month > fiscal_year_end_month else end.year
    return fiscal_year, quarter


def _with_fact_metadata(
    source_item: Mapping[str, Any],
    *,
    priority: int,
    namespace: str,
    concept: str,
    unit: str,
) -> Dict[str, Any]:
    return dict(
        source_item,
        _priority=priority,
        _namespace=namespace,
        _concept=concept,
        _unit=unit,
    )


def _quarterly_candidates(
    company_facts: Mapping[str, Any],
    names: Sequence[str],
    preferred_units: Sequence[str],
    fiscal_year_end_month: int = 6,
    week_based: bool = False,
) -> Dict[Tuple[int, int, str], List[Dict[str, Any]]]:
    buckets: Dict[Tuple[int, int, str], List[Dict[str, Any]]] = {}
    for priority, namespace, concept, fact in _tag_matches(company_facts, names):
        rows, unit = _unit_rows(fact, preferred_units)
        for source_item in rows:
            if source_item.get("form") not in {"10-Q", "10-K", "10-Q/A", "10-K/A"}:
                continue
            try:
                start = date.fromisoformat(str(source_item["start"]))
                end = date.fromisoformat(str(source_item["end"]))
            except (KeyError, TypeError, ValueError):
                continue
            fiscal = _fiscal_period(end, fiscal_year_end_month, week_based=week_based)
            if fiscal is None:
                continue
            fiscal_year, quarter = fiscal
            elapsed = (end - start).days
            fiscal_start = date(
                fiscal_year if fiscal_year_end_month == 12 else fiscal_year - 1,
                fiscal_year_end_month % 12 + 1,
                1,
            )
            begins_at_fiscal_start = abs((start - fiscal_start).days) <= 7
            kind = ""
            if 70 <= elapsed <= 120:
                kind = "direct"
            elif quarter in {2, 3} and begins_at_fiscal_start and 140 <= elapsed <= 300:
                kind = "cumulative"
            elif quarter == 4 and begins_at_fiscal_start and 300 <= elapsed <= 400:
                kind = "annual"
            if kind:
                buckets.setdefault((fiscal_year, quarter, kind), []).append(
                    _with_fact_metadata(
                        source_item,
                        priority=priority,
                        namespace=namespace,
                        concept=concept,
                        unit=unit,
                    )
                )
    return buckets


def _fact_input(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: record.get(key) for key in (
            "fact_id", "metric", "period", "concept", "unit", "raw_value", "value",
            "start", "end", "form", "filed", "accession", "source_id",
        )
    }


def _derived_difference_fact(
    metric: str,
    period: str,
    minuend: Mapping[str, Any],
    subtrahend: Mapping[str, Any],
    *,
    fiscal_year: int,
    quarter: int,
    selection_method: str,
    formula: str,
) -> Dict[str, Any]:
    raw_value = float(minuend["raw_value"]) - float(subtrahend["raw_value"])
    divisor = float(minuend.get("divisor", 1.0))
    value = round(raw_value / divisor, 6)
    inputs = [_fact_input(minuend), _fact_input(subtrahend)]
    identity = {
        "metric": metric,
        "period": period,
        "formula": formula,
        "inputs": [item["fact_id"] for item in inputs],
    }
    fact_id = _stable_fact_id(identity)
    return {
        "fact_id": fact_id,
        "metric": metric,
        "period": period,
        "fiscal_year": fiscal_year,
        "fiscal_quarter": f"Q{quarter}",
        "namespace": "calculated",
        "concept": "DifferenceOfReportedFacts",
        "unit": minuend.get("unit"),
        "normalized_unit": minuend.get("normalized_unit"),
        "raw_value": raw_value,
        "value": value,
        "divisor": divisor,
        "start": None,
        "end": minuend.get("end"),
        "form": "CALCULATED",
        "fiscal_year_reported": None,
        "fiscal_period_reported": None,
        "filed": max(str(minuend.get("filed", "")), str(subtrahend.get("filed", ""))),
        "accession": None,
        "frame": None,
        "source_id": "S-SEC-COMPANYFACTS",
        "selection_method": selection_method,
        "derived": True,
        "formula": formula,
        "inputs": inputs,
        "recast_chain": [],
        "recast_observation_count": sum(
            int(item.get("recast_observation_count", 1)) for item in (minuend, subtrahend)
        ),
        "recast_value_count": None,
        "value_changed_in_recast_chain": any(
            bool(item.get("value_changed_in_recast_chain")) for item in (minuend, subtrahend)
        ),
    }


def _quarterly_metric_lineage(
    company_facts: Mapping[str, Any],
    metric: str,
    names: Sequence[str],
    preferred_units: Sequence[str],
    divisor: float,
    fiscal_year_end_month: int = 6,
    week_based: bool = False,
    consistent_concepts: bool = False,
) -> Dict[str, Dict[str, Any]]:
    candidates = _quarterly_candidates(company_facts, names, preferred_units, fiscal_year_end_month, week_based)
    fiscal_years = sorted({key[0] for key in candidates})
    selected: Dict[str, Dict[str, Any]] = {}
    for fiscal_year in fiscal_years:
        if consistent_concepts:
            annual_candidates = candidates.get((fiscal_year, 4, "annual"), [])
            available = [row for key, rows in candidates.items() if key[0] == fiscal_year for row in rows]
            basis = max(annual_candidates, key=_candidate_rank) if annual_candidates else min(
                available, key=lambda row: int(row.get("_priority", 999))
            )
            for key in [key for key in candidates if key[0] == fiscal_year]:
                candidates[key] = [
                    row for row in candidates[key]
                    if (row.get("_namespace"), row.get("_concept")) == (basis.get("_namespace"), basis.get("_concept"))
                ]
        aggregates: Dict[int, Dict[str, Any]] = {}
        for quarter in (1, 2, 3):
            source = candidates.get((fiscal_year, quarter, "direct"), [])
            if quarter > 1:
                source = candidates.get((fiscal_year, quarter, "cumulative"), [])
            aggregate = _selected_fact(
                metric,
                f"FY{fiscal_year} Q{quarter} {'YTD' if quarter > 1 else 'quarter'}",
                source,
                divisor,
                selection_method="latest_cumulative_recast" if quarter > 1 else "latest_quarter_recast",
            )
            if aggregate is not None:
                aggregates[quarter] = aggregate

        annual = _selected_fact(
            metric,
            f"FY{fiscal_year} annual",
            candidates.get((fiscal_year, 4, "annual"), []),
            divisor,
            selection_method="latest_annual_recast",
        )

        for quarter in (1, 2, 3, 4):
            label = f"FY{fiscal_year} Q{quarter}"
            direct = _selected_fact(
                metric,
                label,
                candidates.get((fiscal_year, quarter, "direct"), []),
                divisor,
                selection_method="latest_quarter_recast",
            )
            record = direct
            if record is None and quarter == 2 and 2 in aggregates and 1 in aggregates:
                record = _derived_difference_fact(
                    metric, label, aggregates[2], aggregates[1], fiscal_year=fiscal_year,
                    quarter=quarter, selection_method="derived_q2_ytd_less_q1",
                    formula="six_month_ytd - first_quarter",
                )
            elif record is None and quarter == 3 and 3 in aggregates and 2 in aggregates:
                record = _derived_difference_fact(
                    metric, label, aggregates[3], aggregates[2], fiscal_year=fiscal_year,
                    quarter=quarter, selection_method="derived_q3_ytd_less_q2_ytd",
                    formula="nine_month_ytd - six_month_ytd",
                )
            elif record is None and quarter == 4 and annual is not None and 3 in aggregates:
                record = _derived_difference_fact(
                    metric, label, annual, aggregates[3], fiscal_year=fiscal_year,
                    quarter=quarter, selection_method="derived_q4_annual_less_q3_ytd",
                    formula="annual - nine_month_ytd",
                )
            if record is not None:
                record["fiscal_year"] = fiscal_year
                record["fiscal_quarter"] = f"Q{quarter}"
                selected[label] = record
    return selected


def _period_ordinal(period: str) -> int:
    match = re.fullmatch(r"FY(\d{4}) Q([1-4])", period)
    if not match:
        return -1
    return int(match.group(1)) * 4 + int(match.group(2)) - 1


def _calculated_fact(
    metric: str,
    period: str,
    value: float,
    inputs: Sequence[Mapping[str, Any]],
    formula: str,
    *,
    normalized_unit: str,
) -> Dict[str, Any]:
    identity = {
        "metric": metric,
        "period": period,
        "formula": formula,
        "inputs": [item.get("fact_id") for item in inputs],
    }
    fact_id = _stable_fact_id(identity)
    return {
        "fact_id": fact_id,
        "metric": metric,
        "period": period,
        "namespace": "calculated",
        "concept": "CalculatedMetric",
        "unit": normalized_unit,
        "normalized_unit": normalized_unit,
        "raw_value": None,
        "value": round(float(value), 6),
        "divisor": 1.0,
        "start": inputs[0].get("start") if inputs else None,
        "end": inputs[-1].get("end") if inputs else None,
        "form": "CALCULATED",
        "filed": max((str(item.get("filed", "")) for item in inputs), default=""),
        "accession": None,
        "frame": None,
        "source_id": "S-SEC-COMPANYFACTS",
        "selection_method": "deterministic_calculation",
        "derived": True,
        "formula": formula,
        "inputs": [_fact_input(item) for item in inputs],
        "recast_chain": [],
        "recast_observation_count": sum(
            int(item.get("recast_observation_count", 1)) for item in inputs
        ),
        "recast_value_count": None,
        "value_changed_in_recast_chain": any(
            bool(item.get("value_changed_in_recast_chain")) for item in inputs
        ),
    }


def normalize_quarterly_facts(
    company_facts: Mapping[str, Any], *, quarters: int = 12,
    fiscal_year_end_month: int = 6,
    currency: str = "USD", week_based: bool = False,
    annual_forms: Sequence[str] = ("10-K",),
    consistent_concepts: bool = False,
) -> Dict[str, Any]:
    """Normalize month-end fiscal quarters and rolling trailing-twelve-months.

    Direct quarter facts are preferred.  When absent, Q2 and Q3 may be derived
    from filed cumulative durations; Q4 is derived only as filed annual less
    filed nine-month YTD.  Every derivation retains its exact input fact IDs.
    """
    if not 1 <= fiscal_year_end_month <= 12:
        raise ValueError("fiscal_year_end_month must be between 1 and 12")
    lineage = {
        metric: _quarterly_metric_lineage(
            company_facts, metric, names, (currency,), divisor, fiscal_year_end_month, week_based,
            consistent_concepts,
        )
        for metric, (names, units, divisor) in QUARTERLY_METRICS.items()
    }
    all_periods = sorted(
        {period for values in lineage.values() for period in values}, key=_period_ordinal
    )
    keep = set(all_periods[-max(4, int(quarters)):])
    lineage = {
        metric: {period: record for period, record in values.items() if period in keep}
        for metric, values in lineage.items()
    }

    cash_flow = lineage.get("operating_cash_flow", {})
    capex = lineage.get("capex", {})
    free_cash_flow: Dict[str, Dict[str, Any]] = {}
    for period in sorted(set(cash_flow) & set(capex), key=_period_ordinal):
        inputs = [cash_flow[period], capex[period]]
        free_cash_flow[period] = _calculated_fact(
            "free_cash_flow",
            period,
            float(inputs[0]["value"]) - float(inputs[1]["value"]),
            inputs,
            "operating_cash_flow - capex",
            normalized_unit=f"{currency} billions",
        )
    lineage["free_cash_flow"] = free_cash_flow
    values = {
        metric: {period: record["value"] for period, record in records.items()}
        for metric, records in lineage.items()
    }

    ttm_lineage: Dict[str, Dict[str, Dict[str, Any]]] = {}
    ttm_series: Dict[str, Dict[str, float]] = {}
    for metric, records in lineage.items():
        ordered = sorted(records, key=_period_ordinal)
        for index in range(3, len(ordered)):
            window = ordered[index - 3:index + 1]
            ordinals = [_period_ordinal(period) for period in window]
            if any(right - left != 1 for left, right in zip(ordinals, ordinals[1:])):
                continue
            inputs = [records[period] for period in window]
            end_period = window[-1]
            record = _calculated_fact(
                metric,
                f"TTM through {end_period}",
                sum(float(item["value"]) for item in inputs),
                inputs,
                "sum of four consecutive fiscal quarters",
                normalized_unit=str(inputs[-1].get("normalized_unit", "")),
            )
            ttm_lineage.setdefault(metric, {})[end_period] = record
            ttm_series.setdefault(metric, {})[end_period] = record["value"]

    latest_period = max(
        (period for records in ttm_series.values() for period in records),
        key=_period_ordinal,
        default=(all_periods[-1] if all_periods else ""),
    )
    latest_ttm = {
        metric: records[latest_period]
        for metric, records in ttm_series.items() if latest_period in records
    }
    revenue = latest_ttm.get("revenue")
    if revenue:
        latest_ttm.update({
            "operating_margin": round(latest_ttm.get("operating_income", 0.0) / revenue, 6),
            "net_margin": round(latest_ttm.get("net_income", 0.0) / revenue, 6),
            "free_cash_flow_margin": round(latest_ttm.get("free_cash_flow", 0.0) / revenue, 6),
            "capex_intensity": round(latest_ttm.get("capex", 0.0) / revenue, 6),
        })

    reconciliation = []
    for metric, (names, units, divisor) in QUARTERLY_METRICS.items():
        annual = annual_series_with_lineage(
            company_facts, metric, names, preferred_units=(currency,), divisor=divisor, years=5,
            forms=annual_forms,
        )
        for year, annual_record in annual.items():
            fiscal_year = int(year)
            periods = [f"FY{fiscal_year} Q{quarter}" for quarter in (1, 2, 3, 4)]
            if not all(period in lineage.get(metric, {}) for period in periods):
                continue
            quarter_sum = round(sum(float(lineage[metric][period]["value"]) for period in periods), 6)
            annual_value = float(annual_record["value"])
            variance = round(quarter_sum - annual_value, 6)
            tolerance = max(0.001, abs(annual_value) * 0.000001)
            reconciliation.append({
                "metric": metric,
                "fiscal_year": f"FY{fiscal_year}",
                "quarter_sum": quarter_sum,
                "annual_value": annual_value,
                "variance": variance,
                "tolerance": tolerance,
                "within_tolerance": abs(variance) <= tolerance,
                "annual_fact_id": annual_record["fact_id"],
                "quarter_fact_ids": [lineage[metric][period]["fact_id"] for period in periods],
            })

    return {
        "quarterly": values,
        "quarterly_lineage": lineage,
        "ttm": latest_ttm,
        "ttm_series": ttm_series,
        "ttm_lineage": ttm_lineage,
        "ttm_as_of": latest_period,
        "reconciliation": reconciliation,
    }


def normalize_company_facts_with_lineage(
    company_facts: Mapping[str, Any], *, years: int = 5, quarters: int = 12,
    fiscal_year_end_month: int = 6, currency: str = "USD", week_based: bool = False,
    year_end_dates: Optional[Sequence[str]] = None,
    annual_forms: Sequence[str] = ("10-K",),
    consistent_concepts: bool = False,
) -> Dict[str, Any]:
    annual_lineage = {
        key: annual_series_with_lineage(
            company_facts, key, names,
            preferred_units=tuple(unit.replace("USD", currency) for unit in units),
            divisor=divisor, years=years, forms=annual_forms,
        )
        for key, (names, units, divisor) in ANNUAL_METRICS.items()
    }
    balance_lineage = {
        key: instant_series_with_lineage(
            company_facts, key, names,
            preferred_units=tuple(unit.replace("USD", currency) for unit in units),
            divisor=divisor, years=3, year_end_dates=year_end_dates, forms=annual_forms,
        )
        for key, (names, units, divisor) in BALANCE_METRICS.items()
    }
    quarterly = normalize_quarterly_facts(
        company_facts, quarters=quarters, fiscal_year_end_month=fiscal_year_end_month,
        currency=currency, week_based=week_based, annual_forms=annual_forms,
        consistent_concepts=consistent_concepts,
    )
    annual = {
        metric: {year: record["value"] for year, record in records.items()}
        for metric, records in annual_lineage.items()
    }
    balance = {
        metric: {year: record["value"] for year, record in records.items()}
        for metric, records in balance_lineage.items()
    }
    fact_index: Dict[str, Dict[str, Any]] = {}

    def index_record(record: Mapping[str, Any]) -> None:
        fact_id = str(record.get("fact_id", ""))
        if not fact_id:
            return
        existing = fact_index.get(fact_id)
        if existing is None or len(record) > len(existing):
            fact_index[fact_id] = dict(record)
        for input_record in record.get("inputs", []):
            if isinstance(input_record, Mapping):
                index_record(input_record)

    collections = [annual_lineage, balance_lineage, quarterly["quarterly_lineage"]]
    for collection in collections:
        for records in collection.values():
            for record in records.values():
                index_record(record)
    for records in quarterly["ttm_lineage"].values():
        for record in records.values():
            index_record(record)
    return {
        "annual": annual,
        "balance_sheet": balance,
        "fact_lineage": {"annual": annual_lineage, "balance_sheet": balance_lineage},
        "fact_index": fact_index,
        **quarterly,
    }


def normalize_company_facts(company_facts: Mapping[str, Any], *, years: int = 5) -> Dict[str, Any]:
    annual = {
        key: annual_series(company_facts, names, preferred_units=units, divisor=divisor, years=years)
        for key, (names, units, divisor) in ANNUAL_METRICS.items()
    }
    balance = {
        key: instant_series(company_facts, names, preferred_units=units, divisor=divisor, years=3)
        for key, (names, units, divisor) in BALANCE_METRICS.items()
    }
    return {"annual": annual, "balance_sheet": balance}


def _attribute(attributes: str, name: str) -> Optional[str]:
    match = re.search(rf"\b{name}\s*=\s*[\"']([^\"']+)[\"']", attributes, re.IGNORECASE)
    return html.unescape(match.group(1)) if match else None


def _numeric_value(body: str, attributes: str) -> Optional[float]:
    text = html.unescape(re.sub(r"<[^>]+>", "", body)).strip()
    text = text.replace("\u2212", "-").replace("—", "").replace("–", "")
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    if cleaned in {"", "-", "."}:
        return None
    try:
        value = float(cleaned)
        scale = int(_attribute(attributes, "scale") or "0")
        value *= 10 ** scale
        if negative or (_attribute(attributes, "sign") or "") == "-":
            value = -abs(value)
        return value
    except (TypeError, ValueError, OverflowError):
        return None


def inline_xbrl_member_data(
    document: str, *, filing: Optional[Mapping[str, Any]] = None
) -> Dict[str, Any]:
    """Extract three-year segment/product values with selected-fact lineage."""
    contexts: Dict[str, Dict[str, Any]] = {}
    for match in re.finditer(
        r"<xbrli:context\b[^>]*\bid=[\"']([^\"']+)[\"'][^>]*>(.*?)</xbrli:context>",
        document,
        re.IGNORECASE | re.DOTALL,
    ):
        block = match.group(2)
        start = re.search(r"<xbrli:startdate>([^<]+)</xbrli:startdate>", block, re.IGNORECASE)
        end = re.search(r"<xbrli:enddate>([^<]+)</xbrli:enddate>", block, re.IGNORECASE)
        instant = re.search(r"<xbrli:instant>([^<]+)</xbrli:instant>", block, re.IGNORECASE)
        members = [html.unescape(re.sub(r"<[^>]+>", "", item)).split(":")[-1]
                   for item in re.findall(r"<xbrldi:explicitmember\b[^>]*>(.*?)</xbrldi:explicitmember>", block, re.IGNORECASE | re.DOTALL)]
        contexts[match.group(1)] = {
            "start": start.group(1) if start else None,
            "end": end.group(1) if end else (instant.group(1) if instant else None),
            "members": members,
        }

    wanted = {
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "CostOfRevenue",
        "OperatingIncomeLoss",
    }
    facts: List[Dict[str, Any]] = []
    for match in re.finditer(r"<ix:nonfraction\b([^>]*)>(.*?)</ix:nonfraction>", document, re.IGNORECASE | re.DOTALL):
        attributes, body = match.group(1), match.group(2)
        qualified_name = _attribute(attributes, "name") or ""
        concept = qualified_name.split(":")[-1]
        if concept not in wanted:
            continue
        context_id = _attribute(attributes, "contextref")
        context = contexts.get(context_id or "")
        value = _numeric_value(body, attributes)
        if not context or value is None:
            continue
        facts.append({
            "concept": concept,
            "namespace": qualified_name.split(":")[0] if ":" in qualified_name else "",
            "context_id": context_id,
            "order": len(facts),
            "value": value,
            **context,
        })

    segment_members = {
        "Productivity and Business Processes": "ProductivityAndBusinessProcessesMember",
        "Intelligent Cloud": "IntelligentCloudMember",
        "More Personal Computing": "MorePersonalComputingMember",
    }
    product_members = {
        "Server products and cloud services": "ServerProductsAndCloudServicesMember",
        "Microsoft 365 Commercial": "MicrosoftThreeSixFiveCommercialProductsAndCloudServicesMember",
        "Gaming": "XBOXMember",
        "LinkedIn": "LinkedInCorporationMember",
        "Windows and Devices": "WindowsAndDevicesMember",
        "Search advertising": "SearchAdvertisingMember",
        "Microsoft 365 Consumer": "MicrosoftThreeSixFiveConsumerProductsAndCloudServicesMember",
        "Dynamics": "DynamicsProductsAndCloudServicesMember",
        "Enterprise and partner services": "EnterpriseAndPartnerServicesMember",
        "Other": "OtherProductsAndServicesMember",
    }

    filing_metadata = dict(filing or {})

    def extract(
        members: Mapping[str, str], concepts: Sequence[str], *, scope: str
    ) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Dict[str, Dict[str, Any]]]]:
        output: Dict[str, Dict[str, float]] = {}
        lineage: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for label, member in members.items():
            candidates: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
            for fact in facts:
                if fact["concept"] not in concepts or member not in fact["members"]:
                    continue
                if not fact.get("start") or not fact.get("end"):
                    continue
                try:
                    elapsed = (date.fromisoformat(fact["end"]) - date.fromisoformat(fact["start"])).days
                except ValueError:
                    continue
                if not 300 <= elapsed <= 400:
                    continue
                year = str(fact["end"])[:4]
                field = {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
                    "CostOfRevenue": "cost_of_revenue",
                    "OperatingIncomeLoss": "operating_income",
                }[fact["concept"]]
                candidates.setdefault((year, field), []).append(fact)
            for (year, field), observations in sorted(candidates.items()):
                # Prefer the least-dimensional context; use the last document
                # occurrence only as a deterministic tie-breaker.
                selected = min(
                    observations,
                    key=lambda item: (len(item.get("members", [])), -int(item.get("order", 0))),
                )
                value = round(float(selected["value"]) / 1e9, 6)
                identity = {
                    "scope": scope,
                    "entity": label,
                    "metric": field,
                    "period": f"FY{year}",
                    "namespace": selected.get("namespace"),
                    "concept": selected.get("concept"),
                    "context_id": selected.get("context_id"),
                    "start": selected.get("start"),
                    "end": selected.get("end"),
                    "raw_value": selected.get("value"),
                    "accession": filing_metadata.get("accessionNumber"),
                }
                fact_id = _stable_fact_id(identity)
                record = {
                    "fact_id": fact_id,
                    "metric": f"{scope}.{field}",
                    "entity": label,
                    "member": member,
                    "period": f"FY{year}",
                    "namespace": selected.get("namespace"),
                    "concept": selected.get("concept"),
                    "unit": "USD",
                    "normalized_unit": "USD billions",
                    "raw_value": selected.get("value"),
                    "value": value,
                    "divisor": 1e9,
                    "start": selected.get("start"),
                    "end": selected.get("end"),
                    "form": filing_metadata.get("form", "10-K"),
                    "filed": filing_metadata.get("filingDate"),
                    "accession": filing_metadata.get("accessionNumber"),
                    "source_id": "S-SEC-10K",
                    "selection_method": "least_dimensional_inline_xbrl_context",
                    "derived": False,
                    "context_id": selected.get("context_id"),
                }
                observation_chain = []
                for observation in sorted(observations, key=lambda item: int(item.get("order", 0))):
                    observation_identity = {
                        **identity,
                        "context_id": observation.get("context_id"),
                        "raw_value": observation.get("value"),
                    }
                    observation_chain.append({
                        "fact_id": _stable_fact_id(observation_identity),
                        "context_id": observation.get("context_id"),
                        "raw_value": observation.get("value"),
                        "value": round(float(observation["value"]) / 1e9, 6),
                        "selected": observation.get("context_id") == selected.get("context_id"),
                    })
                distinct_values = {item["raw_value"] for item in observation_chain}
                record["recast_chain"] = observation_chain
                record["recast_observation_count"] = len(observation_chain)
                record["recast_value_count"] = len(distinct_values)
                record["value_changed_in_recast_chain"] = len(distinct_values) > 1
                output.setdefault(label, {}).setdefault(year, {})[field] = value
                lineage.setdefault(label, {}).setdefault(year, {})[field] = record
        output = {
            label: dict(sorted(by_year.items())[-3:])
            for label, by_year in output.items()
        }
        lineage = {
            label: dict(sorted(by_year.items())[-3:])
            for label, by_year in lineage.items()
        }
        return output, lineage

    segments, segment_lineage = extract(segment_members, tuple(wanted), scope="segment")
    products, product_lineage = extract(
        product_members,
        ("RevenueFromContractWithCustomerExcludingAssessedTax",),
        scope="product",
    )
    inline_fact_index = {
        record["fact_id"]: dict(record)
        for collection in (segment_lineage, product_lineage)
        for by_year in collection.values()
        for by_metric in by_year.values()
        for record in by_metric.values()
    }
    return {
        "segments": segments,
        "products": products,
        "segment_lineage": segment_lineage,
        "product_lineage": product_lineage,
        "inline_fact_index": inline_fact_index,
        "context_count": len(contexts),
    }


def _contains(text: str, *phrases: str) -> bool:
    lowered = text.lower()
    return any(phrase.lower() in lowered for phrase in phrases)


def qualitative_disclosures(document: str, report_year: str) -> Dict[str, Any]:
    text = visible_text(document)
    lowered = text.lower()
    risk_topics = {
        "AI investment returns and adoption": ("investments in cloud and ai", "may not achieve expected returns"),
        "Datacenter capacity, energy, and GPUs": ("datacenters depend on", "graphics processing units", "capacity constraints"),
        "Cybersecurity and service resilience": ("cyberattacks", "security vulnerabilities", "excessive outages"),
        "Privacy and responsible AI": ("responsible ai", "misuse of personal data", "issues about the development, deployment, and use of ai"),
        "Competition and open-source substitution": ("intense competition", "open source offerings"),
        "OpenAI and strategic-partner economics": ("openai", "reciprocal revenue-sharing"),
        "Regulation and antitrust": ("digital markets act", "evolving legal and regulatory requirements"),
        "Tax controversy and transfer pricing": ("notices of proposed adjustment", "transfer pricing"),
        "Supply-chain concentration": ("few qualified suppliers", "supply or other quality problems"),
        "Foreign exchange and geopolitics": ("foreign exchange", "geopolitical"),
        "Government customer exposure": ("government customers", "public-sector"),
        "Talent and execution": ("attract and retain", "qualified employees"),
    }
    topics = []
    for topic, phrases in risk_topics.items():
        hits = sum(lowered.count(phrase.lower()) for phrase in phrases)
        topics.append({"topic": topic, "present": hits > 0, "keyword_hits": hits})

    nopa_match = re.search(
        r"irs\s+is\s+seeking\s+an\s+additional\s+tax\s+payment\s+of\s+\$?([0-9.]+)\s+billion",
        lowered,
    )
    cloud_margin = re.search(r"microsoft cloud gross margin percentage\s+(?:decreased|increased)?\s*to\s+([0-9]+)%", lowered)
    return {
        "text_character_count": len(text),
        "audit": {
            "auditor": "Deloitte & Touche LLP" if "deloitte & touche llp" in lowered else "Not detected",
            "financial_statement_opinion_unmodified": _contains(text, "present fairly, in all material respects"),
            "icfr_opinion_unmodified": _contains(text, "maintained, in all material respects, effective internal control"),
            "management_controls_effective": _contains(text, f"effective as of june 30, {report_year}"),
            "critical_audit_matters": [
                topic for topic, phrases in {
                    "Revenue recognition": ("revenue recognition – refer to note 1", "revenue recognition - refer to note 1"),
                    "Uncertain tax positions": ("income taxes – uncertain tax positions", "income taxes - uncertain tax positions"),
                }.items() if _contains(text, *phrases)
            ],
        },
        "tax": {
            "irs_nopa_claim_usd_b": float(nopa_match.group(1)) if nopa_match else None,
            "pillar_two_discussed": "pillar two" in lowered or "global minimum tax" in lowered,
            "transfer_pricing_discussed": "transfer pricing" in lowered,
        },
        "operations": {
            "microsoft_cloud_gross_margin_pct": float(cloud_margin.group(1)) / 100 if cloud_margin else None,
            "ai_infrastructure_margin_pressure_discussed": (
                "gross margin" in lowered and _contains(
                    text,
                    "driven by continued investments in ai infrastructure",
                    "may decrease our operating margins",
                )
            ),
            "datacenter_resource_constraints_discussed": _contains(text, "datacenters depend on", "capacity constraints"),
        },
        "risk_topics": topics,
    }


def _filing_url(filing: Mapping[str, Any]) -> str:
    accession = str(filing.get("accessionNumber", "")).replace("-", "")
    document = str(filing.get("primaryDocument", ""))
    if not accession or not document:
        return ""
    return (
        f"https://www.sec.gov/Archives/edgar/data/{SEC_CIK_UNPADDED}/"
        f"{accession}/{document}"
    )


def recent_filing_timeline(
    submissions: Mapping[str, Any], *, limit: int = 18
) -> List[Dict[str, Any]]:
    """Normalize recent 10-K, 10-Q, and 8-K metadata from SEC submissions."""
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    wanted = {"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A"}
    fields = (
        "filingDate", "reportDate", "acceptanceDateTime", "form",
        "accessionNumber", "primaryDocument", "isXBRL", "items", "size",
    )
    records: List[Dict[str, Any]] = []
    for index, form in enumerate(forms):
        if form not in wanted:
            continue
        item: Dict[str, Any] = {}
        for field in fields:
            values = recent.get(field, [])
            item[field] = values[index] if index < len(values) else None
        item["url"] = _filing_url(item)
        item["material_event"] = str(form).startswith("8-K")
        records.append(item)
    records.sort(
        key=lambda item: (str(item.get("filingDate", "")), str(item.get("accessionNumber", ""))),
        reverse=True,
    )
    return records[:max(1, int(limit))]


def compare_qualitative_disclosures(
    current: Mapping[str, Any],
    prior: Mapping[str, Any],
    *,
    current_filing: Mapping[str, Any],
    prior_filing: Mapping[str, Any],
) -> Dict[str, Any]:
    """Compare deterministic disclosure signals across two annual filings.

    This is deliberately a topic/attribute delta, not a legal text redline or a
    materiality conclusion.  Keyword counts are retained so a reviewer can see
    exactly why a topic was classified as added, removed, expanded, or reduced.
    """
    current_topics = {item["topic"]: item for item in current.get("risk_topics", [])}
    prior_topics = {item["topic"]: item for item in prior.get("risk_topics", [])}
    rows: List[Dict[str, Any]] = []
    for topic in sorted(set(current_topics) | set(prior_topics)):
        current_item = current_topics.get(topic, {})
        prior_item = prior_topics.get(topic, {})
        current_hits = int(current_item.get("keyword_hits", 0))
        prior_hits = int(prior_item.get("keyword_hits", 0))
        current_present = bool(current_item.get("present"))
        prior_present = bool(prior_item.get("present"))
        change = ""
        severity = "neutral"
        if current_present and not prior_present:
            change, severity = "added signal", "high"
        elif prior_present and not current_present:
            change, severity = "removed signal", "watch"
        elif current_hits >= prior_hits + 2 and current_hits >= max(2, math.ceil(prior_hits * 1.5)):
            change, severity = "expanded signal", "watch"
        elif prior_hits >= current_hits + 2 and prior_hits >= max(2, math.ceil(current_hits * 1.5)):
            change, severity = "reduced signal", "neutral"
        if change:
            rows.append({
                "category": "risk topic",
                "topic": topic,
                "change": change,
                "severity": severity,
                "prior_value": prior_hits,
                "current_value": current_hits,
                "basis": "deterministic phrase-hit delta",
                "evidence_refs": ["S-SEC-10K-PRIOR", "S-SEC-10K"],
            })

    prior_cams = set(prior.get("audit", {}).get("critical_audit_matters", []))
    current_cams = set(current.get("audit", {}).get("critical_audit_matters", []))
    if prior_cams != current_cams:
        rows.append({
            "category": "audit",
            "topic": "Critical audit matters",
            "change": "scope changed",
            "severity": "high",
            "prior_value": sorted(prior_cams),
            "current_value": sorted(current_cams),
            "basis": "detected CAM heading set",
            "evidence_refs": ["S-SEC-10K-PRIOR", "S-SEC-10K"],
        })
    prior_nopa = prior.get("tax", {}).get("irs_nopa_claim_usd_b")
    current_nopa = current.get("tax", {}).get("irs_nopa_claim_usd_b")
    if prior_nopa != current_nopa:
        rows.append({
            "category": "tax",
            "topic": "IRS proposed adjustment amount",
            "change": "reported amount changed",
            "severity": "high",
            "prior_value": prior_nopa,
            "current_value": current_nopa,
            "basis": "deterministic amount extraction; not an expected-loss estimate",
            "evidence_refs": ["S-SEC-10K-PRIOR", "S-SEC-10K"],
        })
    prior_margin = prior.get("operations", {}).get("microsoft_cloud_gross_margin_pct")
    current_margin = current.get("operations", {}).get("microsoft_cloud_gross_margin_pct")
    if prior_margin != current_margin and (prior_margin is not None or current_margin is not None):
        rows.append({
            "category": "operations",
            "topic": "Microsoft Cloud gross margin",
            "change": "reported metric changed",
            "severity": "watch",
            "prior_value": prior_margin,
            "current_value": current_margin,
            "basis": "deterministic percentage extraction",
            "evidence_refs": ["S-SEC-10K-PRIOR", "S-SEC-10K"],
        })
    severity_rank = {"high": 0, "watch": 1, "neutral": 2}
    rows.sort(key=lambda item: (severity_rank.get(item["severity"], 9), item["category"], item["topic"]))
    return {
        "comparison_type": "deterministic topic-level annual disclosure delta",
        "current_filing": {
            "form": current_filing.get("form"),
            "filing_date": current_filing.get("filingDate"),
            "report_date": current_filing.get("reportDate"),
            "accession": current_filing.get("accessionNumber"),
        },
        "prior_filing": {
            "form": prior_filing.get("form"),
            "filing_date": prior_filing.get("filingDate"),
            "report_date": prior_filing.get("reportDate"),
            "accession": prior_filing.get("accessionNumber"),
        },
        "summary": {
            "changed_signals": len(rows),
            "high_attention": sum(item["severity"] == "high" for item in rows),
            "watch": sum(item["severity"] == "watch" for item in rows),
        },
        "changes": rows,
        "limitations": [
            "Phrase-hit movement is a triage signal, not a legal redline or materiality conclusion.",
            "Added or removed wording can reflect drafting, taxonomy, or document-structure changes.",
            "A professional must inspect the linked filings before relying on any disclosure delta.",
        ],
    }


def fetch_microsoft_sec(cache: SourceCache) -> Dict[str, Any]:
    submissions_url = f"https://data.sec.gov/submissions/CIK{SEC_CIK}.json"
    submissions, _ = cache.fetch_json(
        "S-SEC-SUBMISSIONS",
        submissions_url,
        provider="U.S. Securities and Exchange Commission",
        authoritative=True,
        description="Microsoft filing metadata used to discover recent 10-K, 10-Q, and 8-K filings",
        evidence_class="regulatory filing metadata",
        ttl_hours=6,
    )
    filing_history = recent_filing_timeline(submissions, limit=100)
    annual_filings = [item for item in filing_history if item.get("form") == "10-K"]
    quarter_filings = [item for item in filing_history if item.get("form") == "10-Q"]
    timeline = filing_history[:24]
    if len(annual_filings) < 2:
        raise RuntimeError("SEC submissions metadata contained fewer than two Microsoft Forms 10-K")
    if not quarter_filings:
        raise RuntimeError("SEC submissions metadata contained no Microsoft Form 10-Q")
    filing = annual_filings[0]
    prior_filing = annual_filings[1]
    latest_quarter_filing = quarter_filings[0]
    filing_url = _filing_url(filing)
    prior_filing_url = _filing_url(prior_filing)
    latest_quarter_url = _filing_url(latest_quarter_filing)
    if not all((filing_url, prior_filing_url, latest_quarter_url)):
        raise RuntimeError("SEC submissions metadata contained no Microsoft Form 10-K")
    facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{SEC_CIK}.json"
    company_facts, _ = cache.fetch_json(
        "S-SEC-COMPANYFACTS",
        facts_url,
        provider="U.S. Securities and Exchange Commission",
        authoritative=True,
        description="Microsoft standardized XBRL company facts",
        evidence_class="regulatory XBRL facts",
        ttl_hours=6,
    )
    filing_html, _ = cache.fetch_text(
        "S-SEC-10K",
        filing_url,
        provider="U.S. Securities and Exchange Commission",
        authoritative=True,
        description=f"Microsoft Form 10-K for the year ended {filing['reportDate']}",
        evidence_class="filed annual report and audited financial statements",
        ttl_hours=168,
    )
    prior_filing_html, _ = cache.fetch_text(
        "S-SEC-10K-PRIOR",
        prior_filing_url,
        provider="U.S. Securities and Exchange Commission",
        authoritative=True,
        description=f"Prior Microsoft Form 10-K for comparison, year ended {prior_filing['reportDate']}",
        evidence_class="filed annual report and audited financial statements",
        ttl_hours=168,
    )
    latest_quarter_html, _ = cache.fetch_text(
        "S-SEC-10Q-LATEST",
        latest_quarter_url,
        provider="U.S. Securities and Exchange Commission",
        authoritative=True,
        description=f"Latest Microsoft Form 10-Q available in the filing sequence, period ended {latest_quarter_filing['reportDate']}",
        evidence_class="filed interim report and unaudited financial statements",
        ttl_hours=24,
    )
    normalized = normalize_company_facts_with_lineage(company_facts)
    inline = inline_xbrl_member_data(filing_html, filing=filing)
    inline_fact_index = inline.pop("inline_fact_index")
    fact_index = {**normalized["fact_index"], **inline_fact_index}
    fact_lineage = {
        **normalized["fact_lineage"],
        "segments": inline["segment_lineage"],
        "products": inline["product_lineage"],
    }
    report_year = filing["reportDate"][:4]
    current_qualitative = qualitative_disclosures(filing_html, report_year)
    prior_report_year = str(prior_filing.get("reportDate", ""))[:4]
    prior_qualitative = qualitative_disclosures(prior_filing_html, prior_report_year)
    interim_qualitative = qualitative_disclosures(
        latest_quarter_html, str(latest_quarter_filing.get("reportDate", ""))[:4]
    )
    return {
        "company": submissions.get("name", "MICROSOFT CORP"),
        "ticker": TICKER,
        "cik": SEC_CIK,
        "filing": {**filing, "url": filing_url, "fiscal_year": f"FY{report_year}"},
        "prior_filing": {**prior_filing, "url": prior_filing_url, "fiscal_year": f"FY{prior_report_year}"},
        "latest_quarter_filing": {**latest_quarter_filing, "url": latest_quarter_url},
        "filing_timeline": timeline,
        **normalized,
        **inline,
        "fact_lineage": fact_lineage,
        "fact_index": fact_index,
        "qualitative": current_qualitative,
        "prior_qualitative": prior_qualitative,
        "latest_interim_qualitative": interim_qualitative,
        "filing_changes": compare_qualitative_disclosures(
            current_qualitative,
            prior_qualitative,
            current_filing=filing,
            prior_filing=prior_filing,
        ),
        "source_ids": [
            "S-SEC-SUBMISSIONS", "S-SEC-COMPANYFACTS", "S-SEC-10K",
            "S-SEC-10K-PRIOR", "S-SEC-10Q-LATEST",
        ],
    }


def _number(pattern: str, text: str, *, divisor: float = 1.0) -> Optional[float]:
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    try:
        return round(float(match.group(1).replace(",", "")) / divisor, 6)
    except (TypeError, ValueError):
        return None


def fetch_microsoft_investor_relations(cache: SourceCache, fiscal_year: int) -> Dict[str, Any]:
    url = f"https://www.microsoft.com/en-us/Investor/earnings/FY-{fiscal_year}-Q4/press-release-webcast"
    document, _ = cache.fetch_text(
        "S-MSFT-EARNINGS",
        url,
        provider="Microsoft Investor Relations",
        authoritative=True,
        description=f"Microsoft FY{fiscal_year} fourth-quarter and full-year earnings release",
        evidence_class="company-reported earnings release",
        ttl_hours=12,
    )
    text = visible_text(document)
    full_year_heading = re.search(rf"Fiscal Year\s+{fiscal_year}\s+Results", text, re.IGNORECASE)
    if not full_year_heading:
        raise RuntimeError(f"Microsoft earnings release did not contain the FY{fiscal_year} results section")
    full_year_text = text[full_year_heading.end():full_year_heading.end() + 3_500]
    metrics = {
        "fiscal_revenue_usd_b": _number(r"Revenue was \$([0-9,.]+) billion and increased", full_year_text),
        "fiscal_operating_income_usd_b": _number(r"Operating income was \$([0-9,.]+) billion and increased", full_year_text),
        "fiscal_net_income_usd_b": _number(r"Net income was \$([0-9,.]+) billion and increased", full_year_text),
        "fiscal_diluted_eps": _number(r"Diluted earnings per share was \$([0-9,.]+) and increased", full_year_text),
        "azure_revenue_usd_b": _number(r"Azure revenue surpassed \$([0-9,.]+) billion", text),
        "copilot_paid_seats_m": _number(r"Microsoft 365 Copilot reached over ([0-9,.]+) million paid seats", text),
        "quarterly_cloud_revenue_usd_b": _number(r"Microsoft Cloud revenue (?:was|of) \$([0-9,.]+) billion", text),
        "quarterly_cloud_growth": (_number(r"Microsoft Cloud revenue (?:was|of) \$[0-9,.]+ billion[^.]*?up ([0-9.]+)%", text) or 0) / 100 or None,
        "commercial_rpo_usd_b": _number(r"commercial remaining performance obligation increased [0-9.]+% to \$([0-9,.]+) billion", text),
        "commercial_rpo_growth": (_number(r"commercial remaining performance obligation increased ([0-9.]+)%", text) or 0) / 100 or None,
        "azure_growth": (_number(r"Azure and other cloud services revenue increased ([0-9.]+)%", text) or 0) / 100 or None,
        "openai_net_income_impact_usd_b": _number(
            r"increase in net income and diluted earnings per share of \$[0-9,]+ million.*?and \$([0-9,]+) million.*?for the full fiscal year",
            text,
            divisor=1000,
        ),
        "anthropic_gain_usd_b": _number(r"\$([0-9.]+) billion gain from our investment in Anthropic", text),
    }
    return {
        "fiscal_year": f"FY{fiscal_year}",
        "release_url": url,
        "metrics": metrics,
        "source_ids": ["S-MSFT-EARNINGS"],
        "note": "Earnings-release measures are unaudited unless accompanied by an audit opinion; GAAP figures are reconciled to the SEC filing.",
    }


def _yahoo_series(payload: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        result = payload["chart"]["result"][0]
        meta = result["meta"]
        timestamps = result.get("timestamp", [])
        quote = result["indicators"]["quote"][0]
        adjclose = result.get("indicators", {}).get("adjclose", [{}])[0].get("adjclose", [])
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("market-data response schema was not recognized") from exc
    closes = adjclose if adjclose and any(value is not None for value in adjclose) else quote.get("close", [])
    observations = []
    for index, timestamp in enumerate(timestamps):
        close = closes[index] if index < len(closes) else None
        if close is None:
            continue
        volume = quote.get("volume", [None] * len(timestamps))[index]
        observations.append({
            "date": datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat(),
            "close": round(float(close), 6),
            "volume": int(volume) if volume is not None else None,
        })
    if not observations:
        raise RuntimeError("market-data response contained no closing prices")
    return {
        "symbol": meta.get("symbol"),
        "currency": meta.get("currency"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "regular_market_price": meta.get("regularMarketPrice") or observations[-1]["close"],
        "as_of": observations[-1]["date"],
        "observations": observations,
    }


def fetch_market_data(cache: SourceCache) -> Dict[str, Any]:
    base = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=1y&interval=1d&events=div%2Csplits"
    microsoft_url = base.format("MSFT")
    benchmark_url = base.format("%5EGSPC")
    microsoft, _ = cache.fetch_json(
        "S-MARKET-MSFT",
        microsoft_url,
        provider="Yahoo Finance chart endpoint",
        authoritative=False,
        description="Indicative one-year Microsoft adjusted price history; production use requires a licensed feed",
        evidence_class="indicative market data",
        ttl_hours=1,
        extra_headers={"Referer": "https://finance.yahoo.com/"},
    )
    benchmark, _ = cache.fetch_json(
        "S-MARKET-SP500",
        benchmark_url,
        provider="Yahoo Finance chart endpoint",
        authoritative=False,
        description="Indicative one-year S&P 500 adjusted price history used as a market benchmark",
        evidence_class="indicative market data",
        ttl_hours=1,
        extra_headers={"Referer": "https://finance.yahoo.com/"},
    )
    return {
        "microsoft": _yahoo_series(microsoft),
        "benchmark": _yahoo_series(benchmark),
        "source_ids": ["S-MARKET-MSFT", "S-MARKET-SP500"],
        "rights_note": "Indicative market data only. Validate price-sensitive decisions against an approved licensed source.",
    }


def _parse_fred(document: str) -> List[Dict[str, Any]]:
    rows = []
    reader = csv.DictReader(io.StringIO(document))
    value_column = next((name for name in (reader.fieldnames or []) if name != "observation_date"), None)
    if not value_column:
        return rows
    for item in reader:
        raw = item.get(value_column)
        if raw in {None, "", "."}:
            continue
        try:
            rows.append({"date": item["observation_date"], "value": float(raw)})
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def _year_over_year(rows: Sequence[Mapping[str, Any]]) -> Optional[float]:
    if len(rows) < 2:
        return None
    latest_date = date.fromisoformat(str(rows[-1]["date"]))
    target = date(latest_date.year - 1, latest_date.month, 1)
    prior = min(rows[:-1], key=lambda item: abs((date.fromisoformat(str(item["date"])) - target).days))
    if abs((date.fromisoformat(str(prior["date"])) - target).days) > 45 or not prior["value"]:
        return None
    return round(float(rows[-1]["value"]) / float(prior["value"]) - 1, 6)


def fetch_macro_data(cache: SourceCache) -> Dict[str, Any]:
    specifications = {
        "ten_year_treasury": ("DGS10", "Daily 10-year U.S. Treasury constant maturity rate", "%", 48),
        "ten_year_real_yield": ("DFII10", "Daily 10-year inflation-indexed Treasury yield", "%", 48),
        "fed_funds": ("FEDFUNDS", "Monthly effective federal funds rate", "%", 24),
        "cpi": ("CPIAUCSL", "Monthly U.S. consumer price index", "index", 24),
        "real_gdp": ("GDPC1", "Quarterly U.S. real gross domestic product", "USD billions, chained 2017", 12),
        "baa_spread": ("BAA10YM", "Moody's Baa corporate yield relative to 10-year Treasury", "%", 24),
    }
    output: Dict[str, Any] = {}
    source_ids = []
    for key, (series_id, description, unit, keep) in specifications.items():
        source_id = f"S-FRED-{series_id}"
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        document, _ = cache.fetch_csv(
            source_id,
            url,
            provider="FRED, Federal Reserve Bank of St. Louis",
            authoritative=True,
            description=description,
            evidence_class="official macroeconomic series",
            ttl_hours=12,
        )
        rows = _parse_fred(document)
        if not rows:
            raise RuntimeError(f"FRED series {series_id} contained no observations")
        output[key] = {
            "series_id": series_id,
            "unit": unit,
            "as_of": rows[-1]["date"],
            "latest": rows[-1]["value"],
            "year_over_year": _year_over_year(rows) if key in {"cpi", "real_gdp"} else None,
            "observations": rows[-keep:],
        }
        source_ids.append(source_id)
    return {"series": output, "source_ids": source_ids}


PEERS: Tuple[Tuple[str, str, str], ...] = (
    ("GOOGL", "Alphabet", "0001652044"),
    ("AMZN", "Amazon", "0001018724"),
    ("META", "Meta Platforms", "0001326801"),
    ("AAPL", "Apple", "0000320193"),
    ("ORCL", "Oracle", "0001341439"),
)


def _latest_common_year(series: Mapping[str, Mapping[str, float]]) -> Optional[str]:
    required = ("revenue", "operating_income", "operating_cash_flow", "capex")
    sets = [set(series.get(name, {})) for name in required if series.get(name)]
    if not sets:
        return None
    common = set.intersection(*sets)
    return max(common) if common else max(series.get("revenue", {}), default=None)


def _peer_summary(ticker: str, name: str, facts: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = normalize_company_facts(facts, years=3)
    annual = normalized["annual"]
    year = _latest_common_year(annual)
    if not year or year not in annual.get("revenue", {}):
        raise RuntimeError(f"could not identify a comparable annual period for {ticker}")
    revenue = annual["revenue"][year]
    previous_years = sorted(item for item in annual["revenue"] if item < year)
    prior = annual["revenue"].get(previous_years[-1]) if previous_years else None
    operating_income = annual.get("operating_income", {}).get(year)
    cfo = annual.get("operating_cash_flow", {}).get(year)
    capex = annual.get("capex", {}).get(year)
    net_income = annual.get("net_income", {}).get(year)
    return {
        "ticker": ticker,
        "company": name,
        "fiscal_year_end": year,
        "revenue_usd_b": revenue,
        "revenue_growth": revenue / prior - 1 if prior else None,
        "operating_margin": operating_income / revenue if operating_income is not None else None,
        "net_margin": net_income / revenue if net_income is not None else None,
        "free_cash_flow_margin": (cfo - capex) / revenue if cfo is not None and capex is not None else None,
        "capex_intensity": capex / revenue if capex is not None else None,
    }


def fetch_peer_data(cache: SourceCache) -> Dict[str, Any]:
    peers = []
    source_ids = []
    for ticker, name, cik in PEERS:
        source_id = f"S-SEC-PEER-{ticker}"
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        facts, _ = cache.fetch_json(
            source_id,
            url,
            provider="U.S. Securities and Exchange Commission",
            authoritative=True,
            description=f"{name} standardized XBRL company facts for cross-company benchmarking",
            evidence_class="regulatory peer XBRL facts",
            ttl_hours=24,
        )
        try:
            peers.append(_peer_summary(ticker, name, facts))
        except RuntimeError as exc:
            peers.append({"ticker": ticker, "company": name, "error": str(exc)})
        source_ids.append(source_id)
    return {
        "peers": peers,
        "source_ids": source_ids,
        "methodology_note": "Peers use each issuer's latest available annual 10-K period; fiscal calendars and business mixes differ.",
    }


def finite(value: Any, default: float = 0.0) -> float:
    """Convert a numeric value to a finite float for deterministic calculations."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default
