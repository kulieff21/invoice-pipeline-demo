"""Two readers, one invoice: OCR rules and an LLM read the same scan independently.

Where they agree, the value is taken. Where only one of them has a value, that one is taken.
Where they disagree, a check that does not depend on either reader decides: the tax ID's check
digit, the invoice's own arithmetic. If nothing decides, the field is a conflict and the invoice
goes to review. Measured reason (results/*-llm-*.json): the LLM alone drops a repeated digit from
invoice numbers and tax IDs ("10597" -> "1059"), which no business rule can see.
"""

from __future__ import annotations

import re

from invoice_pipeline import taxid
from invoice_pipeline.models import CENT, ExtractionResult, Invoice, LineItem, money
from invoice_pipeline.parse import fold

MONEY_FIELDS = ("subtotal", "tax_amount", "total")


def _same(field: str, a, b) -> bool:
    if field == "vendor_tax_id":
        return taxid.normalize(a) == taxid.normalize(b)
    if field == "vendor_name":
        return re.sub(r"[^0-9a-z]", "", fold(a)) == re.sub(r"[^0-9a-z]", "", fold(b))
    if field == "invoice_number":
        return re.sub(r"\s", "", a).upper() == re.sub(r"\s", "", b).upper()
    return a == b


def _arith_ok(inv: Invoice) -> bool:
    if None in (inv.subtotal, inv.tax_amount, inv.total):
        return False
    ok = abs(inv.subtotal + inv.tax_amount - inv.total) <= CENT
    if inv.tax_rate is not None:
        ok = ok and abs(money(inv.subtotal * inv.tax_rate / 100) - inv.tax_amount) <= CENT
    if inv.line_items:
        ok = ok and abs(money(sum(li.amount for li in inv.line_items)) - inv.subtotal) <= CENT
    return ok


def _line_ok(li: LineItem) -> bool:
    expected = money(li.quantity * li.unit_price * (100 - (li.discount_percent or 0)) / 100)
    return abs(expected - li.amount) <= CENT


def reconcile(rules: ExtractionResult, llm: ExtractionResult) -> ExtractionResult:
    a, b = rules.invoice, llm.invoice
    out = b.model_copy(deep=True)
    notes = list(rules.notes) + list(llm.notes)
    conflicts: list[str] = []

    for field in ("vendor_name", "vendor_tax_id", "invoice_number", "issue_date", "due_date", "currency",
                  "tax_rate"):
        va, vb = getattr(a, field), getattr(b, field)
        if va is None or vb is None or _same(field, va, vb):
            setattr(out, field, vb if vb is not None else va)
            continue
        if field == "vendor_tax_id":
            # only a real check digit or a known national format is evidence; a generic
            # "two letters and digits" match is not (an LLM's "CH76950391" once beat the printed,
            # correctly flagged "CHE76950391" that way)
            ca, cb = taxid.check(va), taxid.check(vb)
            ok_a = ca[0] and ca[1] != "unknown-country"
            ok_b = cb[0] and cb[1] != "unknown-country"
            if ok_a != ok_b:
                setattr(out, field, va if ok_a else vb)
                notes.append(f"tax ID: readers disagree ({va} / {vb}); kept the one that passes its check")
                continue
        if field == "vendor_name":
            notes.append(f"vendor name: readers disagree ('{va}' / '{vb}')")
            continue  # low stakes, the vendor registry may overrule it later
        # undecided: the row keeps the OCR reading (keys stay stable across re-runs); the LLM's
        # reading is in the conflict message the reviewer sees
        setattr(out, field, va)
        conflicts.append(f"{field}: OCR read '{va}', LLM read '{vb}'")

    # amounts: pick the reading under which the invoice adds up
    trial_a = out.model_copy(update={f: getattr(a, f) for f in MONEY_FIELDS})
    trial_b = out.model_copy(update={f: getattr(b, f) for f in MONEY_FIELDS})
    differs = [f for f in MONEY_FIELDS if None not in (getattr(a, f), getattr(b, f)) and getattr(a, f) != getattr(b, f)]
    if differs:
        if _arith_ok(trial_b):
            pass
        elif _arith_ok(trial_a):
            for f in MONEY_FIELDS:
                setattr(out, f, getattr(a, f))
            notes.append("amounts: kept the OCR reading, which adds up")
        else:
            conflicts.append("amounts: readers disagree and neither reading adds up: "
                             + ", ".join(f"{f} {getattr(a, f)} / {getattr(b, f)}" for f in differs))
    for f in MONEY_FIELDS:
        if getattr(out, f) is None and getattr(a, f) is not None:
            setattr(out, f, getattr(a, f))

    # line items: per line, the reading whose own arithmetic holds
    if len(a.line_items) == len(b.line_items) and a.line_items:
        merged = []
        for i, (la, lb) in enumerate(zip(a.line_items, b.line_items), 1):
            same = (la.quantity, la.unit_price, la.amount) == (lb.quantity, lb.unit_price, lb.amount)
            if same or _line_ok(lb):
                merged.append(lb)
            elif _line_ok(la):
                merged.append(la.model_copy(update={"description": lb.description}))
                notes.append(f"line {i}: kept the OCR numbers, which multiply out")
            else:
                merged.append(lb)
                conflicts.append(f"line {i}: neither reading multiplies out")
        out.line_items = merged
    elif not b.line_items and a.line_items:
        out.line_items = a.line_items

    found = sum(getattr(out, f) is not None for f in
                ("vendor_name", "vendor_tax_id", "invoice_number", "issue_date", "currency", "total"))
    return ExtractionResult(invoice=out, method="ocr+llm", confidence=round(found / 6, 3), raw_text=rules.raw_text,
                            notes=notes, conflicts=conflicts)

