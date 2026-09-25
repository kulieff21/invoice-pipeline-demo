from datetime import date
from decimal import Decimal as D

from invoice_pipeline.models import Invoice, LineItem
from invoice_pipeline.validate import validate

AS_OF = date(2026, 9, 25)


def clean() -> Invoice:
    return Invoice(
        vendor_name="Example GmbH", vendor_tax_id="DE136695976", invoice_number="RE-1", issue_date=date(2026, 9, 1),
        due_date=date(2026, 10, 1), currency="EUR", subtotal=D("150.00"), tax_rate=D("19"), tax_amount=D("28.50"),
        total=D("178.50"),
        line_items=[LineItem(description="a", quantity=D("2"), unit_price=D("50.00"), amount=D("100.00")),
                    LineItem(description="b", quantity=D("0.5"), unit_price=D("100.00"), amount=D("50.00"))])


def codes(inv, **kw):
    return {i.code for i in validate(inv, AS_OF, **kw)}


def test_clean_invoice_passes():
    assert codes(clean()) == set()


def test_each_rule_fires_alone():
    inv = clean(); inv.line_items[0].amount = D("101.00"); inv.subtotal = D("151.00")
    inv.tax_amount = D("28.69"); inv.total = D("179.69")
    assert codes(inv) == {"LINE_AMOUNT_MISMATCH"}
    inv = clean(); inv.tax_amount = D("27.00"); inv.total = D("177.00")
    assert codes(inv) == {"TAX_MISMATCH"}
    inv = clean(); inv.total = D("188.50")
    assert codes(inv) == {"TOTAL_MISMATCH"}
    inv = clean(); inv.subtotal = D("140.00"); inv.tax_amount = D("26.60"); inv.total = D("166.60")
    assert codes(inv) == {"LINES_SUM_MISMATCH"}
    inv = clean(); inv.due_date = date(2026, 8, 1)
    assert codes(inv) == {"DUE_BEFORE_ISSUE"}
    inv = clean(); inv.issue_date = date(2026, 12, 1); inv.due_date = date(2026, 12, 31)
    assert codes(inv) == {"FUTURE_DATE"}
    inv = clean(); inv.vendor_tax_id = "DE136695977"
    assert codes(inv) == {"INVALID_TAX_ID"}
    inv = clean(); inv.invoice_number = None
    assert codes(inv) == {"MISSING_INVOICE_NUMBER"}


def test_rounding_within_a_cent_is_accepted():
    inv = clean(); inv.subtotal = D("150.03"); inv.line_items[1].amount = D("50.03")
    inv.line_items[1].unit_price = D("100.06"); inv.tax_amount = D("28.51"); inv.total = D("178.54")
    assert codes(inv) == set()


def test_low_confidence_goes_to_review():
    assert "LOW_CONFIDENCE" in codes(clean(), confidence=0.5)
