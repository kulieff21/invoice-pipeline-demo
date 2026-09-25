"""Holdout layouts, written after the extraction rules were frozen (git tag `rules-frozen`).

They differ from the development layouts in structure, not just wording:
- nl: labels stacked above their values in a band, quantity as the first table column, an extra
  "VAT %" column inside the table, ISO dates, "€ 1.234,56".
- it: label and value inside one text run without a colon ("Fattura n. 2026/0147"), amounts
  without any currency symbol (the currency appears once, in "Importi in EUR").
"""

from __future__ import annotations

import textwrap

from decimal import Decimal

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


# --- holdout-2, written after the rules-frozen-v2 tag -------------------------------------------------
# es: summary box with the total above the table, a discount column (qty x price != amount on
#     discounted lines), "Nº Factura", NIF.
# ch: Swiss German, CHF, apostrophe thousands ("1'234.50"), Swiss UID "CHE-123.456.789 MWST".


def render_es(doc: Doc) -> bytes:
    p = Page(A4)
    m = lambda v: f"{group(v, '.', ',')} €"  # noqa: E731
    d = lambda v: v.strftime("%d/%m/%Y")  # noqa: E731
    y = p.h - 55
    p.text(50, y, doc.vendor.name, size=14, bold=True)
    p.text(50, y - 14, f"NIF: {doc.tax_id or ''}", size=8)
    p.text(50, y - 24, f"{doc.vendor.street}, {doc.vendor.city}", size=8)
    p.c.rect(p.w - 230, y - 62, 180, 70)
    p.text(p.w - 220, y - 8, "FACTURA", size=14, bold=True)
    if doc.number is not None:
        p.text(p.w - 220, y - 24, f"Nº Factura: {doc.number}", size=9)
    p.text(p.w - 220, y - 37, f"Fecha: {d(doc.issue)}", size=9)
    p.text(p.w - 220, y - 50, f"Total factura: {m(doc.total)}", size=9, bold=True)
    y -= 95
    p.text(50, y, "Cliente", size=8, bold=True)
    _address(p, 50, y - 13, doc.customer, bold_name=True)
    y -= 85
    cols = [("Concepto", 50, False), ("Cantidad", 330, True), ("Precio", 400, True), ("Dto. %", 450, True),
            ("Importe", p.w - 50, True)]
    for header, cx, right in cols:
        p.text(cx, y, header, bold=True, right=right)
    y -= 5
    p.rule(50, y, p.w - 50)
    y -= 13
    discounts = doc.discounts or [Decimal(0)] * len(doc.lines)
    for (desc, qty, unit, amount), disc in zip(doc.lines, discounts):
        desc_lines = textwrap.wrap(desc, 46) or [""]
        p.text(50, y, desc_lines[0])
        p.text(330, y, qty_text(qty, ","), right=True)
        p.text(400, y, m(unit), right=True)
        p.text(450, y, f"{rate_text(disc, ',')} %" if disc else "", right=True)
        p.text(p.w - 50, y, m(amount), right=True)
        for extra in desc_lines[1:]:
            y -= 11
            p.text(50, y, extra)
        y -= 15
    p.rule(50, y + 8, p.w - 50)
    y -= 10
    for label, value, bold in [("Base imponible", m(doc.subtotal), False),
                               (f"IVA ({rate_text(doc.tax_rate, ',')} %)", m(doc.tax), False),
                               ("Total", m(doc.total), True)]:
        p.text(380, y, label, bold=bold)
        p.text(p.w - 50, y, value, bold=bold, right=True)
        y -= 15
    p.text(50, 70, f"Vencimiento: {d(doc.due)}. Forma de pago: transferencia.", size=8)
    return p.finish()


def _chf(v) -> str:
    return group(v, "'", ".")


def _uid(tax_id: str | None) -> str:
    if tax_id and tax_id.startswith("CHE") and len(tax_id) == 12:
        digits = tax_id[3:]
        return f"CHE-{digits[:3]}.{digits[3:6]}.{digits[6:]} MWST"
    return tax_id or ""


def render_ch(doc: Doc) -> bytes:
    p = Page(A4)
    d = lambda v: v.strftime("%d.%m.%Y")  # noqa: E731
    y = p.h - 55
    p.text(p.w - 50, y, doc.vendor.name, size=13, bold=True, right=True)
    p.text(p.w - 50, y - 13, f"{doc.vendor.street}, {doc.vendor.city}", size=8, right=True)
    p.text(p.w - 50, y - 23, _uid(doc.tax_id), size=8, right=True)
    y -= 70
    _address(p, 50, y, doc.customer, bold_name=False)
    y -= 70
    title = f"Rechnung Nr. {doc.number}" if doc.number is not None else "Rechnung"
    p.text(50, y, title, size=14, bold=True)
    p.text(50, y - 16, f"Datum: {d(doc.issue)}   Zahlbar bis: {d(doc.due)}", size=9)
    y -= 45
    cols = [("Menge", 50, False), ("Bezeichnung", 100, False), ("Preis CHF", 430, True), ("Total CHF", p.w - 50, True)]
    for header, cx, right in cols:
        p.text(cx, y, header, bold=True, right=right)
    y -= 5
    p.rule(50, y, p.w - 50)
    y -= 13
    for desc, qty, unit, amount in doc.lines:
        desc_lines = textwrap.wrap(desc, 52) or [""]
        p.text(50, y, qty_text(qty, "."))
        p.text(100, y, desc_lines[0])
        p.text(430, y, _chf(unit), right=True)
        p.text(p.w - 50, y, _chf(amount), right=True)
        for extra in desc_lines[1:]:
            y -= 11
            p.text(100, y, extra)
        y -= 15
    p.rule(50, y + 8, p.w - 50)
    y -= 10
    for label, value, bold in [("Zwischentotal", _chf(doc.subtotal), False),
                               (f"MWST {rate_text(doc.tax_rate, '.')}%", _chf(doc.tax), False),
                               ("Rechnungsbetrag CHF", _chf(doc.total), True)]:
        p.text(340, y, label, bold=bold)
        p.text(p.w - 50, y, value, bold=bold, right=True)
        y -= 15
    return p.finish()


RENDERERS = {"nl": render_nl, "it": render_it, "es": render_es, "ch": render_ch}
