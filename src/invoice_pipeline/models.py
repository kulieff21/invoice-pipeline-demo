"""Data model shared by the pipeline, the evaluator and the synthetic data generator."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

CENT = Decimal("0.01")


def money(value: Decimal | int | str) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


class LineItem(BaseModel):
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    discount_percent: Decimal | None = None


class Invoice(BaseModel):
    """What is printed on the document. Extraction targets this, errors included."""

    vendor_name: str | None = None
    vendor_tax_id: str | None = None
    invoice_number: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax_rate: Decimal | None = None  # percent, e.g. 19 or 8.875
    tax_amount: Decimal | None = None
    total: Decimal | None = None
    line_items: list[LineItem] = Field(default_factory=list)


FIELDS = [name for name in Invoice.model_fields if name != "line_items"]


class Severity(StrEnum):
    ERROR = "error"
    REVIEW = "review"


class Issue(BaseModel):
    code: str
    severity: Severity
    message: str


class Status(StrEnum):
    OK = "ok"
    NEEDS_REVIEW = "needs_review"
    DUPLICATE = "duplicate"  # same vendor + invoice number already processed; no new row


class ExtractionResult(BaseModel):
    invoice: Invoice
    method: str  # "text" | "ocr" | "llm"
    confidence: float  # 0..1, share of required fields the extractor was sure about
    raw_text: str = ""
    notes: list[str] = Field(default_factory=list)  # repairs and inferences, shown to the reviewer
    conflicts: list[str] = Field(default_factory=list)  # OCR and LLM disagree and nothing decides
