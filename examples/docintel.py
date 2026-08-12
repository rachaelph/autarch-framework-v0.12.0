"""Azure Document Intelligence (prebuilt-invoice) extraction for ``extract_invoice.py``.

This is the process's **step 5** done properly: instead of vision-OCR + an LLM guessing the fields,
the PDF is sent to Azure Document Intelligence's ``prebuilt-invoice`` model, which returns the
structured header fields AND the line items **with a per-field confidence score**, with no
per-vendor training. Two nice consequences that align with the tax flow:

  * the **governing state** is read from the SHIPPING address (``ShippingAddress`` -> ``ServiceAddress``
    -> ``CustomerAddress``), never the bill-to / vendor address; and
  * every extracted value carries DI's own confidence, which feeds the field verdict + routing.

Auth mirrors the MAF path: Entra ID pinned to the resource tenant (``AZURE_DOCINTEL_TENANT_ID`` /
``AZURE_OPENAI_TENANT_ID``) or an API key (``AZURE_DOCINTEL_KEY``). Degrades gracefully - any failure
returns ``{"error": ...}`` (or ``None`` when the SDK isn't installed) and the caller falls back to the
vision-OCR + LLM path.
"""
from __future__ import annotations

import os
import re


def _num(v):
    """First numeric token of ``v`` as a plain number string (drops $ and thousands separators)."""
    if v is None:
        return ""
    m = re.search(r"[-+]?\d[\d,]*\.?\d*", str(v).replace("$", ""))
    return m.group().replace(",", "") if m else ""


def _scalar(field):
    """Best scalar value + confidence from a DI DocumentField (currency/number/string/date)."""
    if field is None:
        return None, None
    conf = getattr(field, "confidence", None)
    cur = getattr(field, "value_currency", None)
    if cur is not None and getattr(cur, "amount", None) is not None:
        return cur.amount, conf
    for attr in ("value_number", "value_string", "value_date"):
        v = getattr(field, attr, None)
        if v is not None:
            return (str(v) if attr == "value_date" else v), conf
    return getattr(field, "content", None), conf


def _state_from_address(field):
    """State code from a DI address field's ``value_address.state``, else a regex on its content."""
    if field is None:
        return "", None
    conf = getattr(field, "confidence", None)
    addr = getattr(field, "value_address", None)
    if addr is not None and getattr(addr, "state", None):
        return str(addr.state).strip().upper(), conf
    content = getattr(field, "content", "") or ""
    m = re.search(r"\b([A-Z]{2})\s+\d{5}", content)
    return (m.group(1) if m else ""), conf


def analyze_invoice(pdf_path, endpoint, tenant_id=None, api_key=None):
    """Analyze the invoice PDF with DI ``prebuilt-invoice``. Returns a dict:
    ``{header, confidence, lines, content, n_pages, state_source}`` on success, ``{"error": ...}`` on
    failure, or ``None`` when the SDK is not installed."""
    try:
        from azure.ai.documentintelligence import DocumentIntelligenceClient
    except Exception:
        return None
    try:
        if api_key:
            from azure.core.credentials import AzureKeyCredential
            cred = AzureKeyCredential(api_key)
        elif tenant_id:
            from azure.identity import AzureCliCredential
            cred = AzureCliCredential(tenant_id=tenant_id)
        else:
            from azure.identity import AzureCliCredential, ChainedTokenCredential, DefaultAzureCredential
            cred = ChainedTokenCredential(AzureCliCredential(), DefaultAzureCredential())
        client = DocumentIntelligenceClient(endpoint=endpoint, credential=cred)
        with open(pdf_path, "rb") as fh:
            pdf_bytes = fh.read()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    # A freshly-granted data-plane role is eventually consistent: some backend nodes accept the call
    # while others still reject it for a few minutes. Retry a handful of times on that transient
    # PermissionDenied blip before giving up (the caller then falls back to OCR+LLM).
    import time
    last_exc = None
    for attempt in range(4):
        try:
            poller = client.begin_analyze_document("prebuilt-invoice", body=pdf_bytes, content_type="application/pdf")
            result = poller.result()
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            msg = str(exc).lower()
            transient = "does not have access to api" in msg or "authorizationpermissionmismatch" in msg
            if transient and attempt < 3:
                time.sleep(3)
                continue
            return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    else:
        return {"error": f"{type(last_exc).__name__}: {str(last_exc)[:200]}"}

    docs = result.documents or []
    fields = (docs[0].fields or {}) if docs else {}
    header, confidence = {}, {}

    def put(our_key, di_key, numeric=False):
        val, c = _scalar(fields.get(di_key))
        if val is None or not str(val).strip():
            return
        header[our_key] = _num(val) if numeric else str(val).strip()
        if c is not None:
            confidence[our_key] = round(float(c), 3)

    put("vendor_name", "VendorName")
    put("invoice_number", "InvoiceId")
    put("invoice_date", "InvoiceDate")
    put("po_number", "PurchaseOrder")
    put("subtotal", "SubTotal", numeric=True)
    put("total_amount", "InvoiceTotal", numeric=True)
    put("tax_charged", "TotalTax", numeric=True)

    # Governing STATE from the SHIP-TO / service address, never bill-to (tax-flow rule).
    state_source = ""
    for akey in ("ShippingAddress", "ServiceAddress", "CustomerAddress"):
        st, c = _state_from_address(fields.get(akey))
        if st:
            header["state"] = st
            state_source = akey
            if c is not None:
                confidence["state"] = round(float(c), 3)
            break

    # Line items with their own fields.
    lines = []
    items_field = fields.get("Items")
    for it in (getattr(items_field, "value_array", None) or []):
        obj = getattr(it, "value_object", None) or {}

        def cell(k):
            return _scalar(obj.get(k))[0]

        desc = cell("Description")
        if desc and str(desc).strip():
            lines.append({
                "description": str(desc).strip(),
                "quantity": _num(cell("Quantity")),
                "unit_price": _num(cell("UnitPrice")),
                "amount": _num(cell("Amount")),
            })

    return {
        "header": header,
        "confidence": confidence,
        "lines": lines,
        "content": result.content or "",
        "n_pages": len(result.pages or []),
        "state_source": state_source,
    }
