"""Business rules. Every rule returns an issue code; any issue sends the invoice to review.

The pipeline never "fixes" a vendor's arithmetic: a total that does not add up is reported, not
recalculated, because the payable amount is a business decision.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from invoice_pipeline import taxid
from invoice_pipeline.models import Invoice, Issue, Severity, money

TOLERANCE = Decimal("0.01")
REQUIRED = ["vendor_name", "vendor_tax_id", "invoice_number", "issue_date", "currency", "total"]


def _issue(code: str, message: str, severity: Severity = Severity.ERROR) -> Issue:
    return Issue(code=code, severity=severity, message=message)


def validate(inv: Invoice, as_of: date, confidence: float | None = None, min_confidence: float = 0.8) -> list[Issue]:
    issues: list[Issue] = []
    for field in REQUIRED:
        if getattr(inv, field) in (None, ""):
            issues.append(_issue(f"MISSING_{field.upper()}", f"{field} not found on the document"))

    if inv.vendor_tax_id:
        ok, how = taxid.check(inv.vendor_tax_id)
        if not ok:
            issues.append(_issue("INVALID_TAX_ID", f"{inv.vendor_tax_id} fails the {how} check"))

    for i, line in enumerate(inv.line_items, 1):
        expected = money(line.quantity * line.unit_price)
        if abs(expected - line.amount) > TOLERANCE:
            issues.append(_issue("LINE_AMOUNT_MISMATCH",
                                 f"line {i}: {line.quantity} x {line.unit_price} = {expected}, printed {line.amount}"))
    if not inv.line_items:
        issues.append(_issue("NO_LINE_ITEMS", "no line items could be read", Severity.REVIEW))
    elif inv.subtotal is not None:
        lines_sum = money(sum(line.amount for line in inv.line_items))
        if abs(lines_sum - inv.subtotal) > TOLERANCE:
            issues.append(_issue("LINES_SUM_MISMATCH", f"lines add up to {lines_sum}, subtotal is {inv.subtotal}"))

    if inv.subtotal is not None and inv.tax_amount is not None and inv.tax_rate is not None:
        expected_tax = money(inv.subtotal * inv.tax_rate / 100)
        if abs(expected_tax - inv.tax_amount) > TOLERANCE:
            issues.append(_issue("TAX_MISMATCH",
                                 f"{inv.tax_rate}% of {inv.subtotal} is {expected_tax}, printed {inv.tax_amount}"))
    if None not in (inv.subtotal, inv.tax_amount, inv.total):
        if abs(inv.subtotal + inv.tax_amount - inv.total) > TOLERANCE:
            issues.append(_issue("TOTAL_MISMATCH",
                                 f"{inv.subtotal} + {inv.tax_amount} != printed total {inv.total}"))

    if inv.issue_date and inv.due_date and inv.due_date < inv.issue_date:
        issues.append(_issue("DUE_BEFORE_ISSUE", f"due {inv.due_date} is before issue {inv.issue_date}"))
    if inv.issue_date and inv.issue_date > as_of:
        issues.append(_issue("FUTURE_DATE", f"issue date {inv.issue_date} is after {as_of}"))

    if confidence is not None and confidence < min_confidence:
        issues.append(_issue("LOW_CONFIDENCE", f"extraction confidence {confidence:.2f}", Severity.REVIEW))
    return issues
