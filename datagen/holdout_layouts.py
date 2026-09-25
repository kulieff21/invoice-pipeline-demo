"""Holdout layouts, written after the extraction rules were frozen (git tag `rules-frozen`).

They differ from the development layouts in structure, not just wording:
- nl: labels stacked above their values in a band, quantity as the first table column, an extra
  "VAT %" column inside the table, ISO dates, "€ 1.234,56".
- it: label and value inside one text run without a colon ("Fattura n. 2026/0147"), amounts
  without any currency symbol (the currency appears once, in "Importi in EUR").
"""

from __future__ import annotations

import textwrap

from reportlab.lib.pagesizes import A4

from datagen.layouts import Doc, Page, _address, group, qty_text, rate_text


def render_nl(doc: Doc) -> bytes:
    p = Page(A4)
    m = lambda v: f"€ {group(v, '.', ',')}"  # noqa: E731
    d = lambda v: v.isoformat()  # noqa: E731
    y = p.h - 60
    p.text(50, y, doc.vendor.name, size=16, bold=True)
    p.text(50, y - 16, f"{doc.vendor.street}, {doc.vendor.city}, {doc.vendor.country}", size=8)
    p.text(50, y - 27, f"VAT number {doc.tax_id or ''}", size=8)
    p.text(p.w - 50, y, "Invoice", size=18, right=True)
    y -= 70
    p.text(50, y, "Invoice for", size=8, bold=True)
    _address(p, 50, y - 13, doc.customer, bold_name=False)
    y -= 85
    band = [("Invoice number", doc.number), ("Invoice date", d(doc.issue)), ("Due date", d(doc.due)),
            ("Payment terms", f"{(doc.due - doc.issue).days} days")]
    x = 50
    for label, value in band:
        if value is not None:
            p.text(x, y, label, size=8, bold=True)
            p.text(x, y - 13, value, size=10)
        x += 125
    y -= 45
    cols = [("Qty", 50, False), ("Description", 90, False), ("Unit price", 390, True), ("VAT %", 450, True),
            ("Amount", p.w - 50, True)]
    for header, cx, right in cols:
        p.text(cx, y, header, bold=True, right=right)
    y -= 5
    p.rule(50, y, p.w - 50)
    y -= 13
    for desc, qty, unit, amount in doc.lines:
        desc_lines = textwrap.wrap(desc, 50) or [""]
        p.text(50, y, qty_text(qty, ","))
        p.text(90, y, desc_lines[0])
        p.text(390, y, m(unit), right=True)
        p.text(450, y, f"{rate_text(doc.tax_rate, ',')}%", right=True)
        p.text(p.w - 50, y, m(amount), right=True)
        for extra in desc_lines[1:]:
            y -= 11
            p.text(90, y, extra)
        y -= 15
    p.rule(50, y + 8, p.w - 50)
    y -= 10
    for label, value, bold in [("Subtotal", m(doc.subtotal), False),
                               (f"VAT {rate_text(doc.tax_rate, ',')}%", m(doc.tax), False),
                               ("Total", m(doc.total), True)]:
        p.text(p.w - 200, y, label, bold=bold)
        p.text(p.w - 50, y, value, bold=bold, right=True)
        y -= 16
    p.text(50, 60, f"Please pay within {(doc.due - doc.issue).days} days quoting the invoice number.", size=8)
    return p.finish()


def render_it(doc: Doc) -> bytes:
    p = Page(A4)
    m = lambda v: group(v, ".", ",")  # noqa: E731
    d = lambda v: v.strftime("%d/%m/%Y")  # noqa: E731
    y = p.h - 55
    p.text(50, y, doc.vendor.name, size=15, bold=True)
    p.text(50, y - 15, doc.vendor.street, size=8)
    p.text(50, y - 25, f"{doc.vendor.city} ({doc.vendor.country})", size=8)
    p.text(50, y - 35, f"Partita IVA: {doc.tax_id or ''}", size=8)
    p.text(p.w - 50, y, "FATTURA", size=18, bold=True, right=True)
    if doc.number is not None:
        p.text(p.w - 50, y - 20, f"Fattura n. {doc.number}", size=10, right=True)
    p.text(p.w - 50, y - 34, f"Data: {d(doc.issue)}", size=9, right=True)
    y -= 80
    p.text(p.w - 240, y, "Spett.le", size=8)
    _address(p, p.w - 240, y - 13, doc.customer, bold_name=True)
    y -= 90
    p.text(50, y, "Importi in EUR", size=8)
    y -= 18
    cols = [("Descrizione", 50, False), ("Quantità", 350, True), ("Prezzo unitario", 450, True),
            ("Importo", p.w - 50, True)]
    for header, cx, right in cols:
        p.text(cx, y, header, bold=True, right=right)
    y -= 5
    p.rule(50, y, p.w - 50)
    y -= 13
    for desc, qty, unit, amount in doc.lines:
        desc_lines = textwrap.wrap(desc, 48) or [""]
        p.text(50, y, desc_lines[0])
        p.text(350, y, qty_text(qty, ","), right=True)
        p.text(450, y, m(unit), right=True)
        p.text(p.w - 50, y, m(amount), right=True)
        for extra in desc_lines[1:]:
            y -= 11
            p.text(50, y, extra)
        y -= 15
    p.rule(50, y + 8, p.w - 50)
    y -= 10
    for label, value, bold in [("Imponibile", m(doc.subtotal), False),
                               (f"IVA {rate_text(doc.tax_rate, ',')}%", m(doc.tax), False),
                               ("Totale documento", m(doc.total), True)]:
        p.text(360, y, label, bold=bold)
        p.text(p.w - 50, y, value, bold=bold, right=True)
        y -= 15
    p.text(50, 70, f"Scadenza: {d(doc.due)}  -  Pagamento: bonifico bancario", size=8)
    return p.finish()


RENDERERS = {"nl": render_nl, "it": render_it}
