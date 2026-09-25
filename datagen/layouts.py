"""Invoice layouts drawn with reportlab. Each layout has its own language, number and date format.

Development layouts: uk, de, us, fr. The extraction rules are written against these.
"""

from __future__ import annotations

import io
import textwrap
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from reportlab.lib.pagesizes import A4, LETTER
from reportlab.pdfgen.canvas import Canvas

MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass
class Party:
    name: str
    street: str
    city: str
    country: str


@dataclass
class Doc:
    layout: str
    vendor: Party
    customer: Party
    tax_id: str | None
    number: str | None
    issue: date
    due: date
    currency: str
    lines: list[tuple[str, Decimal, Decimal, Decimal]]  # description, qty, unit, amount
    subtotal: Decimal
    tax_rate: Decimal
    tax: Decimal
    total: Decimal
    discounts: list[Decimal] | None = None  # per-line discount percent (holdout-2 "es" layout)


def group(value: Decimal, thousands: str, decimal_sep: str) -> str:
    sign = "-" if value < 0 else ""
    whole, frac = f"{abs(value):.2f}".split(".")
    parts = []
    while len(whole) > 3:
        parts.insert(0, whole[-3:])
        whole = whole[:-3]
    parts.insert(0, whole)
    return sign + thousands.join(parts) + decimal_sep + frac


def qty_text(q: Decimal, decimal_sep: str) -> str:
    text = f"{q.normalize():f}"
    return text.replace(".", decimal_sep)


def rate_text(r: Decimal, decimal_sep: str) -> str:
    return f"{r.normalize():f}".replace(".", decimal_sep)


SYMBOL = {"GBP": "£", "EUR": "€", "USD": "$", "CHF": "CHF "}


class Page:
    def __init__(self, size):
        self.buf = io.BytesIO()
        self.c = Canvas(self.buf, pagesize=size, invariant=1)
        self.w, self.h = size

    def text(self, x, y, s, size=9, bold=False, right=False):
        self.c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        if right:
            self.c.drawRightString(x, y, s)
        else:
            self.c.drawString(x, y, s)

    def rule(self, x1, y, x2, width=0.5):
        self.c.setLineWidth(width)
        self.c.line(x1, y, x2, y)

    def finish(self) -> bytes:
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()


def _table(p: Page, doc: Doc, y: float, cols: list[tuple[str, float, bool]], fmt_row, wrap: int) -> float:
    """cols: (header, x, right_aligned). Returns y after the table."""
    for header, x, right in cols:
        p.text(x, y, header, bold=True, right=right)
    y -= 5
    p.rule(cols[0][1], y, cols[-1][1])
    y -= 13
    for line in doc.lines:
        desc_lines = textwrap.wrap(line[0], wrap) or [""]
        values = fmt_row(line)
        p.text(cols[0][1], y, desc_lines[0])
        for (_, x, right), value in zip(cols[1:], values):
            p.text(x, y, value, right=right)
        for extra in desc_lines[1:]:
            y -= 11
            p.text(cols[0][1], y, extra)
        y -= 15
    p.rule(cols[0][1], y + 8, cols[-1][1])
    return y - 8


def _address(p: Page, x: float, y: float, party: Party, size=9, bold_name=True) -> float:
    p.text(x, y, party.name, size=size + 1, bold=bold_name)
    for s in (party.street, party.city, party.country):
        y -= size + 3
        p.text(x, y, s, size=size)
    return y


def render_uk(doc: Doc) -> bytes:
    p = Page(A4)
    sym = SYMBOL[doc.currency]
    m = lambda v: f"{sym}{group(v, ',', '.')}"  # noqa: E731
    d = lambda v: f"{v.day:02d} {MONTHS_EN[v.month - 1]} {v.year}"  # noqa: E731
    y = p.h - 60
    _address(p, 50, y, doc.vendor, size=9)
    p.text(p.w - 50, y, "INVOICE", size=22, bold=True, right=True)
    y -= 70
    meta = [
        ("Invoice No:", doc.number or ""),
        ("Invoice Date:", d(doc.issue)),
        ("Due Date:", d(doc.due)),
        ("VAT Reg No:", _spaced_gb(doc.tax_id)),
    ]
    my = y
    for label, value in meta:
        if label == "Invoice No:" and doc.number is None:
            continue
        p.text(p.w - 220, my, label, bold=True)
        p.text(p.w - 50, my, value, right=True)
        my -= 14
    p.text(50, y, "Bill To", bold=True)
    _address(p, 50, y - 14, doc.customer, bold_name=False)
    y -= 90
    cols = [("Description", 50, False), ("Qty", 360, True), ("Unit Price", 450, True), ("Amount", p.w - 50, True)]
    y = _table(p, doc, y, cols, lambda ln: (qty_text(ln[1], "."), m(ln[2]), m(ln[3])), 52)
    for label, value, bold in [
        ("Subtotal", m(doc.subtotal), False),
        (f"VAT ({rate_text(doc.tax_rate, '.')}%)", m(doc.tax), False),
        ("Total Due", m(doc.total), True),
    ]:
        p.text(400, y, label, bold=bold)
        p.text(p.w - 50, y, value, bold=bold, right=True)
        y -= 15
    p.text(50, 60, f"Payment terms: {(doc.due - doc.issue).days} days. Thank you for your business.", size=8)
    return p.finish()


def _spaced_gb(tax_id: str | None) -> str:
    if tax_id and tax_id.startswith("GB") and len(tax_id) == 11:
        return f"GB {tax_id[2:5]} {tax_id[5:9]} {tax_id[9:]}"
    return tax_id or ""


def render_de(doc: Doc) -> bytes:
    p = Page(A4)
    m = lambda v: f"{group(v, '.', ',')} €"  # noqa: E731
    d = lambda v: v.strftime("%d.%m.%Y")  # noqa: E731
    y = p.h - 50
    p.text(p.w - 50, y, doc.vendor.name, size=14, bold=True, right=True)
    p.text(p.w - 50, y - 16, f"{doc.vendor.street} · {doc.vendor.city}", size=8, right=True)
    y -= 70
    p.text(50, y, f"{doc.vendor.name} · {doc.vendor.street} · {doc.vendor.city}", size=6)
    _address(p, 50, y - 16, doc.customer, bold_name=False)
    my = y - 16
    for label, value in [
        ("Rechnungsnummer:", doc.number),
        ("Rechnungsdatum:", d(doc.issue)),
        ("Fällig am:", d(doc.due)),
        ("USt-IdNr.:", doc.tax_id or ""),
    ]:
        if value is None:
            continue
        p.text(360, my, label)
        p.text(p.w - 50, my, value, right=True)
        my -= 13
    y -= 110
    p.text(50, y, "RECHNUNG", size=16, bold=True)
    y -= 28
    cols = [("Pos.", 50, False), ("Beschreibung", 80, False), ("Menge", 360, True), ("Einzelpreis", 450, True), ("Betrag", p.w - 50, True)]
    for header, x, right in cols:
        p.text(x, y, header, bold=True, right=right)
    y -= 5
    p.rule(50, y, p.w - 50)
    y -= 13
    for i, (desc, qty, unit, amount) in enumerate(doc.lines, 1):
        desc_lines = textwrap.wrap(desc, 48) or [""]
        p.text(50, y, str(i))
        p.text(80, y, desc_lines[0])
        p.text(360, y, qty_text(qty, ","), right=True)
        p.text(450, y, m(unit), right=True)
        p.text(p.w - 50, y, m(amount), right=True)
        for extra in desc_lines[1:]:
            y -= 11
            p.text(80, y, extra)
        y -= 15
    p.rule(50, y + 8, p.w - 50)
    y -= 8
    for label, value, bold in [
        ("Nettobetrag", m(doc.subtotal), False),
        (f"zzgl. MwSt. {rate_text(doc.tax_rate, ',')} %", m(doc.tax), False),
        ("Gesamtbetrag", m(doc.total), True),
    ]:
        p.text(360, y, label, bold=bold)
        p.text(p.w - 50, y, value, bold=bold, right=True)
        y -= 15
    p.text(50, 70, f"Zahlbar bis {d(doc.due)} ohne Abzug.", size=8)
    return p.finish()


def render_us(doc: Doc) -> bytes:
    p = Page(LETTER)
    m = lambda v: f"${group(v, ',', '.')}"  # noqa: E731
    d = lambda v: v.strftime("%m/%d/%Y")  # noqa: E731
    y = p.h - 55
    p.text(50, y, doc.vendor.name.upper(), size=13, bold=True)
    p.text(50, y - 14, doc.vendor.street, size=8)
    p.text(50, y - 24, doc.vendor.city, size=8)
    p.text(50, y - 34, f"Tax ID (EIN): {doc.tax_id or ''}", size=8)
    p.text(p.w - 50, y, "Invoice", size=26, right=True)
    my = y - 30
    for label, value in [("Invoice #", doc.number), ("Date", d(doc.issue)), ("Due", d(doc.due))]:
        if value is None:
            continue
        p.text(p.w - 150, my, label, bold=True, right=True)
        p.text(p.w - 50, my, value, right=True)
        my -= 13
    y -= 90
    p.text(50, y, "BILL TO", size=8, bold=True)
    _address(p, 50, y - 13, doc.customer, bold_name=False)
    y -= 85
    cols = [("Item", 50, False), ("Qty", 380, True), ("Rate", 470, True), ("Amount", p.w - 50, True)]
    y = _table(p, doc, y, cols, lambda ln: (qty_text(ln[1], "."), m(ln[2]), m(ln[3])), 56)
    for label, value, bold in [
        ("Subtotal", m(doc.subtotal), False),
        (f"Sales Tax ({rate_text(doc.tax_rate, '.')}%)", m(doc.tax), False),
        ("Balance Due", m(doc.total), True),
    ]:
        p.text(420, y, label, bold=bold, right=True)
        p.text(p.w - 50, y, value, bold=bold, right=True)
        y -= 15
    return p.finish()


def render_fr(doc: Doc) -> bytes:
    p = Page(A4)
    m = lambda v: f"{group(v, ' ', ',')} €"  # noqa: E731
    d = lambda v: v.strftime("%d/%m/%Y")  # noqa: E731
    y = p.h - 55
    _address(p, 50, y, doc.vendor)
    p.text(50, y - 50, f"N° TVA : {doc.tax_id or ''}", size=8)
    p.text(p.w - 50, y, "FACTURE", size=20, bold=True, right=True)
    my = y - 24
    for label, value in [
        ("Facture N° :", doc.number),
        ("Date d'émission :", d(doc.issue)),
        ("Échéance :", d(doc.due)),
    ]:
        if value is None:
            continue
        p.text(p.w - 200, my, label)
        p.text(p.w - 50, my, value, right=True)
        my -= 13
    y -= 90
    p.c.rect(p.w - 250, y - 60, 200, 72)
    p.text(p.w - 240, y, "Client", bold=True)
    _address(p, p.w - 240, y - 14, doc.customer, bold_name=False)
    y -= 95
    cols = [("Désignation", 50, False), ("Qté", 340, True), ("Prix unitaire HT", 450, True), ("Montant HT", p.w - 50, True)]
    y = _table(p, doc, y, cols, lambda ln: (qty_text(ln[1], ","), m(ln[2]), m(ln[3])), 50)
    for label, value, bold in [
        ("Total HT", m(doc.subtotal), False),
        (f"TVA {rate_text(doc.tax_rate, ',')} %", m(doc.tax), False),
        ("Total TTC", m(doc.total), True),
    ]:
        p.text(380, y, label, bold=bold)
        p.text(p.w - 50, y, value, bold=bold, right=True)
        y -= 15
    p.text(50, 60, "Pénalités de retard : trois fois le taux d'intérêt légal.", size=7)
    return p.finish()


RENDERERS = {"uk": render_uk, "de": render_de, "us": render_us, "fr": render_fr}
