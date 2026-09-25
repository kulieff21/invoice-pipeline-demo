from datetime import date
from decimal import Decimal as D

from invoice_pipeline.models import ExtractionResult, Invoice, LineItem
from invoice_pipeline.reconcile import reconcile


def inv(**kw) -> Invoice:
    base = dict(vendor_name="Example GmbH", vendor_tax_id="DE136695976", invoice_number="10597",
                issue_date=date(2026, 9, 1), due_date=date(2026, 10, 1), currency="EUR", subtotal=D("100.00"),
                tax_rate=D("19"), tax_amount=D("19.00"), total=D("119.00"),
                line_items=[LineItem(description="a", quantity=D("2"), unit_price=D("50.00"), amount=D("100.00"))])
    base.update(kw)
    return Invoice(**base)


def r(i: Invoice, method="ocr") -> ExtractionResult:
    return ExtractionResult(invoice=i, method=method, confidence=1.0)


def test_agreement_passes_through():
    out = reconcile(r(inv()), r(inv(), "llm"))
    assert out.conflicts == [] and out.invoice.invoice_number == "10597"


def test_dropped_digit_in_invoice_number_is_a_conflict():
    out = reconcile(r(inv()), r(inv(invoice_number="1059"), "llm"))
    assert any("invoice_number" in c for c in out.conflicts)


def test_check_digit_decides_the_tax_id():
    out = reconcile(r(inv()), r(inv(vendor_tax_id="DE13669597"), "llm"))
    assert out.invoice.vendor_tax_id == "DE136695976" and out.conflicts == []


def test_a_generic_format_match_is_not_evidence():
    printed_typo = "CHE76950391"  # one digit short: the vendor's error, correctly read by OCR
    out = reconcile(r(inv(vendor_tax_id=printed_typo)), r(inv(vendor_tax_id="CH76950391"), "llm"))
    assert out.conflicts, "must not let the LLM's variant pass as valid"


def test_arithmetic_decides_amounts():
    out = reconcile(r(inv()), r(inv(total=D("11.90")), "llm"))
    assert out.invoice.total == D("119.00")


def test_missing_value_is_filled_from_the_other_reader():
    out = reconcile(r(inv(currency=None)), r(inv(), "llm"))
    assert out.invoice.currency == "EUR"
