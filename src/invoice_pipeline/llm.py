"""LLM fallback for scanned pages the rules could not read cleanly.

Only called for OCR'd documents that failed validation (missing field, arithmetic that does not
add up, low confidence). The model reads the PDF page itself and returns the printed values as
JSON under a strict schema; the result goes through the same validation as everything else, so
a model mistake still ends in the review queue rather than in the sheet as "ok".

Credentials: ANTHROPIC_API_KEY (or an `ant auth login` profile). Model: INVOICE_LLM_MODEL.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from invoice_pipeline.models import ExtractionResult, Invoice, LineItem

DEFAULT_MODEL = "claude-opus-5"

SYSTEM = """You transcribe invoices into JSON for an accounts-payable system.

Copy what is printed. Do not correct the vendor's arithmetic, do not compute missing values, do
not guess: a field that is not printed or not legible is null. The pipeline checks the numbers
itself and needs to see errors exactly as printed.

Formats:
- amounts, quantities, rates: plain decimal strings with a dot, no thousands separator, no
  currency symbol ("1234.56", "39.75", "8.875")
- dates: ISO "YYYY-MM-DD"; decide day/month order from the document's language and other dates
- currency: ISO 4217 code of the printed symbol or code ("€" -> "EUR", "£" -> "GBP")
- vendor_tax_id: the seller's VAT / tax ID as printed, spaces removed
- vendor_name: the seller (letterhead), not the customer in "Bill to"
- line_items: every billed line in order; join a description that wraps onto a second line"""

NULLABLE_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}
SCHEMA = {
    "type": "object",
    "properties": {
        "vendor_name": NULLABLE_STR,
        "vendor_tax_id": NULLABLE_STR,
        "invoice_number": NULLABLE_STR,
        "issue_date": NULLABLE_STR,
        "due_date": NULLABLE_STR,
        "currency": NULLABLE_STR,
        "subtotal": NULLABLE_STR,
        "tax_rate": NULLABLE_STR,
        "tax_amount": NULLABLE_STR,
        "total": NULLABLE_STR,
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "quantity": {"type": "string"},
                    "unit_price": {"type": "string"},
                    "amount": {"type": "string"},
                },
                "required": ["description", "quantity", "unit_price", "amount"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["vendor_name", "vendor_tax_id", "invoice_number", "issue_date", "due_date", "currency",
                 "subtotal", "tax_rate", "tax_amount", "total", "line_items"],
    "additionalProperties": False,
}

REQUIRED = ["vendor_name", "vendor_tax_id", "invoice_number", "issue_date", "currency", "total"]


def _dec(v: str | None) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(v)
    except InvalidOperation:
        return None


def _date(v: str | None) -> date | None:
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


def to_invoice(data: dict) -> Invoice:
    items = []
    for li in data.get("line_items") or []:
        q, u, a = _dec(li["quantity"]), _dec(li["unit_price"]), _dec(li["amount"])
        if None not in (q, u, a):
            items.append(LineItem(description=li["description"], quantity=q, unit_price=u, amount=a))
    return Invoice(
        vendor_name=data.get("vendor_name"),
        vendor_tax_id=(data.get("vendor_tax_id") or "").replace(" ", "") or None,
        invoice_number=data.get("invoice_number"),
        issue_date=_date(data.get("issue_date")),
        due_date=_date(data.get("due_date")),
        currency=(data.get("currency") or "").upper() or None,
        subtotal=_dec(data.get("subtotal")),
        tax_rate=_dec(data.get("tax_rate")),
        tax_amount=_dec(data.get("tax_amount")),
        total=_dec(data.get("total")),
        line_items=items,
    )


class LLMFallback:
    def __init__(self, model: str | None = None, client=None):
        self.model = model or os.environ.get("INVOICE_LLM_MODEL", DEFAULT_MODEL)
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "refusals": 0}

    def __call__(self, pdf_path: str, rules_result: ExtractionResult) -> ExtractionResult | None:
        pdf = base64.standard_b64encode(Path(pdf_path).read_bytes()).decode()
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # a declined request is retried on a fallback model server-side
            system=SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf}},
                    {"type": "text", "text": "Transcribe this invoice."},
                ],
            }],
        )
        self.usage["calls"] += 1
        self.usage["input_tokens"] += response.usage.input_tokens
        self.usage["output_tokens"] += response.usage.output_tokens
        if response.stop_reason == "refusal":
            self.usage["refusals"] += 1
            return None  # keep the rules result; the invoice stays in review
        if response.stop_reason == "max_tokens":
            return None
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            return None
        invoice = to_invoice(json.loads(text))
        found = sum(getattr(invoice, f) is not None for f in REQUIRED)
        return ExtractionResult(
            invoice=invoice,
            method="ocr+llm",
            confidence=round(found / len(REQUIRED), 3),
            raw_text=rules_result.raw_text,
            notes=[f"fields read by {response.model} after rule-based extraction failed validation"],
        )
