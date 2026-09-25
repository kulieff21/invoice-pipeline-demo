"""Rule-based field extraction over positioned cells.

Nothing here knows a specific template. Labels are a multilingual vocabulary (EN, DE, FR, IT, ES,
NL); values are found to the right of a label, inside the same cell after a colon, or directly
below it. Line items are read from the right (amount, unit price, quantity), so descriptions may
contain digits and may wrap onto a second line.
"""

from __future__ import annotations

import re
from decimal import Decimal

from invoice_pipeline.layout import Cell, Line, PageText
from invoice_pipeline.models import ExtractionResult, Invoice, LineItem, money
from invoice_pipeline.parse import (
    CURRENCY_CODES,
    date_parts,
    find_currency,
    fold,
    parse_amount,
    parse_date,
    parse_number,
    parse_percent,
)

LABELS: dict[str, list[str]] = {
    "invoice_number": [
        "invoiceno", "invoicenumber", "invoice#", "invoiceid", "inv#", "invno",
        "rechnungsnummer", "rechnungsnr", "rechnung-nr", "rechnungnr",
        "facturen°", "facturen", "facturenº", "numerodefacture", "nfacture", "n°facture",
        "fatturan", "fatturanr", "numerofattura", "fatturan.",
        "facturanº", "numerodefactura", "nºfactura", "facturanum",
        "factuurnummer", "factuurnr",
    ],
    "issue_date": [
        "invoicedate", "dateofissue", "issuedate", "issued", "date",
        "rechnungsdatum", "datum", "dated'emission", "datedefacture", "datedefacturation",
        "datafattura", "data", "fechadefactura", "fechadeemision", "fecha", "factuurdatum",
    ],
    "due_date": [
        "duedate", "due", "paymentdue", "payby", "falligam", "fallig", "falligkeit", "zahlbarbis",
        "echeance", "dated'echeance", "datedecheance", "scadenza", "datascadenza",
        "vencimiento", "fechadevencimiento", "vervaldatum", "uiterlijkbetalen",
    ],
    "vendor_tax_id": [
        "vatregno", "vatregistrationnumber", "vatno", "vatnumber", "vatid", "vatreg", "taxid", "ein",
        "ust-idnr", "ustidnr", "ust-id", "ustid", "steuernummer", "mwst-nr", "mwstnr", "uid", "uid-nr",
        "n°tva", "ntva", "numerotva", "tvaintracommunautaire", "n°tvaintracom",
        "partitaiva", "p.iva", "piva", "nif", "cif", "btw-nummer", "btwnummer", "btw-nr", "btwnr",
    ],
    "subtotal": [
        "subtotal", "sub-total", "netamount", "totalnet", "totalexcl", "totalexclvat", "net",
        "nettobetrag", "netto", "summenetto", "zwischensumme", "zwischentotal",
        "totalht", "sous-total",
        "imponibile", "totaleimponibile", "baseimponible", "subtotaal", "totaalexcl",
    ],
    "tax_amount": [
        "vat", "salestax", "tax", "gst",
        "mwst", "zzgl.mwst", "zzgl.ust", "ust", "umsatzsteuer",
        "tva", "montanttva", "iva", "btw",
    ],
    "total": [
        "totaldue", "balancedue", "amountdue", "grandtotal", "total", "totalamount", "invoicetotal",
        "gesamtbetrag", "gesamtsumme", "rechnungsbetrag", "endbetrag", "gesamt",
        "totalttc", "netapayer", "montantttc",
        "totale", "totaledocumento", "totaleapagare", "totalfactura", "importetotal",
        "totaal", "totaalbedrag", "tebetalen",
    ],
}

TITLES = {"invoice", "rechnung", "facture", "fattura", "factura", "factuur", "taxinvoice", "billto"}

HEADER_WORDS = {
    "description", "item", "items", "qty", "quantity", "unitprice", "price", "rate", "amount", "total",
    "pos", "pos.", "beschreibung", "bezeichnung", "menge", "anzahl", "einzelpreis", "betrag", "gesamt",
    "designation", "qte", "prixunitaireht", "prixunitaire", "montantht", "montant",
    "descrizione", "quantita", "qta", "prezzo", "prezzounitario", "importo",
    "omschrijving", "aantal", "prijs", "bedrag", "concepto", "cantidad", "precio", "importe",
}

REQUIRED = ["vendor_name", "vendor_tax_id", "invoice_number", "issue_date", "currency", "total"]

# country prefix + at least six consecutive digits (so "ROSEANDSONS" is not an ID), or a US EIN;
# a malformed EIN is still captured so the validator can reject it instead of calling it missing
TAX_ID_RE = re.compile(r"\b(CHE-?\d{9}|[A-Z]{2}(?=[0-9A-Z]*\d{6})[0-9A-Z]{8,12}|\d{2}-\d{5,8})\b")
# OCR glues words ("VATnumberNL433768154B49"), so a second pass accepts a known VAT country prefix
# without a word boundary in front of it
VAT_PREFIXED_RE = re.compile(
    r"(?:AT|BE|BG|CY|CZ|DE|DK|EE|EL|ES|FI|FR|GB|HR|HU|IE|IT|LT|LU|LV|MT|NL|PL|PT|RO|SE|SI|SK|CH|NO)"
    r"(?=[0-9A-Z]*\d{6})[0-9A-Z]{8,12}\b")
EUROZONE = {"AT", "BE", "CY", "DE", "EE", "ES", "FI", "FR", "GR", "EL", "HR", "IE", "IT", "LT", "LU", "LV",
            "MT", "NL", "PT", "SI", "SK"}
NUMERIC_CELL_RE = re.compile(r"^[-+]?[\d\s.,' ]*\d[\d\s.,' ]*$")


def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _label_match(cell_text: str) -> tuple[str, str] | None:
    """Best (field, label) for a cell, longest label wins. Exact prefix first, then 1-edit fuzzy
    for labels of 6+ characters (OCR drops or swaps letters)."""
    f = fold(cell_text)
    exact: tuple[int, str, str] | None = None
    fuzzy: tuple[int, str, str] | None = None
    for field, labels in LABELS.items():
        for label in labels:
            head, rest = f[: len(label)], f[len(label):]
            if rest and rest[0].isalpha() and not _label_then_value(cell_text, label):
                continue
            if head == label:
                if exact is None or len(label) > exact[0]:
                    exact = (len(label), field, label)
            elif len(label) >= 6 and len(head) == len(label) and _levenshtein(head, label) <= 1:
                if fuzzy is None or len(label) > fuzzy[0]:
                    fuzzy = (len(label), field, label)
    # OCR 'USt-ldNr' must not fall back to the short exact label 'ust' (tax amount)
    if fuzzy and (exact is None or fuzzy[0] > exact[0] + 2):
        return fuzzy[1], fuzzy[2]
    return (exact[1], exact[2]) if exact else None


def _strip_currency(text: str) -> str:
    out = text
    for sym in "£€$¥":
        out = out.replace(sym, "")
    for code in CURRENCY_CODES:
        out = out.replace(code, "")
    return out.strip()


def is_numeric_cell(text: str) -> bool:
    return bool(NUMERIC_CELL_RE.match(_strip_currency(text)))


def _label_then_value(text: str, label: str) -> bool:
    """'VAT number NL8829…': the label is followed by a word boundary in the printed text and the
    rest carries digits. Keeps 'vat' from matching 'VAT Reg No' and 'data' from 'Data Systems Ltd'."""
    for i in range(1, len(text) + 1):
        if fold(text[:i]) == label:
            if i >= len(text) or text[i].isalnum():
                return False
            rest = text[i:].strip(" :.")
            # "Rechnungsbetrag CHF": a currency code after the label is part of the label
            return any(ch.isdigit() for ch in rest) or rest.upper() in CURRENCY_CODES
    return False


def _compact_ids(text: str) -> str:
    """'GB 526 0181 74' -> 'GB526018174' without gluing neighbouring words together."""
    text = text.upper().replace(".", "").replace("CHE-", "CHE")
    text = re.sub(r"(?<=\d) (?=\d)", "", text)
    return re.sub(r"\b([A-Z]{2}) (?=\d)", r"\1", text)


RATE_CELL_RE = re.compile(r"^[-+]?\d{1,2}(?:[.,]\d{1,3})?\s*%$")


def _after_label(text: str, label: str) -> str | None:
    """Text that follows the label inside the same cell: 'Fattura n. 2026/0147' -> '2026/0147'."""
    for i in range(1, len(text) + 1):
        if fold(text[:i]) == label:
            rest = text[i:].strip(" :#.°º-")
            return rest or None
    return None


def _after_colon(text: str) -> str | None:
    for sep in (":", "#", "°", "º"):
        if sep in text:
            rest = text.split(sep, 1)[1].strip(" :#.")
            if rest:
                return rest
    return None


class Extractor:
    def __init__(self, page: PageText):
        self.page = page
        self.lines = page.lines

    # --- value lookup ---------------------------------------------------------------------------
    def _candidates(self, li: int, ci: int, label: str) -> list[str]:
        line = self.lines[li]
        cell = line.cells[ci]
        out: list[str] = []
        inline = _after_colon(cell.text) or _after_label(cell.text, label)
        if inline:
            out.append(inline)
        out.extend(c.text for c in line.cells[ci + 1 :])
        if li + 1 < len(self.lines):  # stacked: value directly below the label
            below = self.lines[li + 1]
            if below.top - line.top < 3 * cell.size:
                for c in below.cells:
                    if c.x0 < cell.x1 + 5 and c.x1 > cell.x0 - 5:
                        out.append(c.text)
        return out

    def _label_hits(self) -> dict[str, list[tuple[int, int, str, str]]]:
        hits: dict[str, list[tuple[int, int, str, str]]] = {}
        for li, line in enumerate(self.lines):
            if self._is_header(line):  # column headers ("Montant HT") are not field labels
                continue
            for ci, cell in enumerate(line.cells):
                m = _label_match(cell.text)
                if m:
                    hits.setdefault(m[0], []).append((li, ci, cell.text, m[1]))
        return hits

    # --- document-level conventions ---------------------------------------------------------------
    def _decimal_sep(self) -> str:
        comma = dot = 0
        for line in self.lines:
            for c in line.cells:
                t = _strip_currency(c.text)
                if re.search(r",\d{2}$", t):
                    comma += 1
                elif re.search(r"\.\d{2}$", t):
                    dot += 1
        return "," if comma > dot else "."

    def _date_order(self, texts: list[str], currency: str | None) -> str:
        for t in texts:
            parts = date_parts(t)
            if parts and not re.search(r"\d{4}[./-]", t):
                a, b, _ = parts
                if a > 12:
                    return "dmy"
                if b > 12:
                    return "mdy"
        full = fold(self.page.text)
        if currency == "USD" or "salestax" in full or "(ein)" in full:
            return "mdy"
        return "dmy"

    # --- fields ------------------------------------------------------------------------------------
    def vendor_name(self) -> str | None:
        limit = self.page.height * 0.3
        cands: list[Cell] = []
        for line in self.lines:
            for c in line.cells:
                if c.top > limit:
                    continue
                f = fold(c.text).strip(":.")
                if f in TITLES or _label_match(c.text) or is_numeric_cell(c.text) or len(f) < 3:
                    continue
                if parse_date(c.text) or TAX_ID_RE.search(c.text.replace(" ", "")):
                    continue
                digits = sum(ch.isdigit() for ch in c.text)
                if digits > 0.3 * len(c.text.replace(" ", "")):
                    continue  # invoice numbers, postcodes, street numbers
                cands.append(c)
        if not cands:
            return None
        biggest = max(c.size for c in cands)
        if self.page.source == "text":
            # font sizes are exact: the largest non-title text, bold first, then highest on the page
            top = [c for c in cands if c.size >= biggest - 0.5]
            top.sort(key=lambda c: (not c.bold, c.top))
        else:
            # OCR box heights swing with descenders (',', 'g', 'y'), so size only filters out small
            # print; among the rest the letterhead is the highest line
            top = [c for c in cands if c.size >= biggest * 0.75]
            top.sort(key=lambda c: c.top)
        return top[0].text.strip()

    def extract(self) -> ExtractionResult:
        hits = self._label_hits()
        values: dict[str, list[str]] = {f: [] for f in LABELS}
        label_text: dict[str, str] = {}
        for field, positions in hits.items():
            for li, ci, text, label in positions:
                values[field].extend(self._candidates(li, ci, label))
                label_text.setdefault(field, text)

        all_text = self.page.text
        currency = None
        symbols: dict[str, int] = {}
        for line in self.lines:
            for c in line.cells:
                cur = find_currency(c.text)
                if cur:
                    symbols[cur] = symbols.get(cur, 0) + 1
        inferred: list[str] = []
        if symbols:
            currency = max(symbols, key=symbols.get)

        inv = Invoice()
        inv.vendor_name = self.vendor_name()

        for text in values["vendor_tax_id"]:
            m = TAX_ID_RE.search(_compact_ids(text))
            if m:
                inv.vendor_tax_id = m.group(1)
                break
        if inv.vendor_tax_id is None:
            # no labelled ID: look for one cell by cell (joining the whole page glued two ISO dates
            # into an "EIN" once) and never inside a date
            for line in self.lines:
                for c in line.cells:
                    if parse_date(c.text):
                        continue
                    compact = _compact_ids(c.text)
                    m = TAX_ID_RE.search(compact) or VAT_PREFIXED_RE.search(compact)
                    if m:
                        inv.vendor_tax_id = m.group(m.lastindex or 0)
                        inferred.append("vendor_tax_id")
                        break
                if inv.vendor_tax_id:
                    break

        for text in values["invoice_number"]:
            candidate = text.strip(" :#.°º")
            if re.search(r"\d", candidate) and not parse_date(candidate) and len(candidate) <= 30:
                inv.invoice_number = candidate
                break

        self.notes: list[str] = []
        if currency is None and inv.vendor_tax_id:
            # A symbol the OCR cannot read (it drops € and £) is only filled in where the vendor's
            # country has one currency in practice; a UK vendor may bill in GBP or EUR -> review.
            prefix = inv.vendor_tax_id[:2]
            if prefix in EUROZONE:
                currency = "EUR"
            elif re.fullmatch(r"\d{2}-\d+", inv.vendor_tax_id):
                currency = "USD"
            if currency:
                inferred.append("currency")
                self.notes.append(f"currency {currency} inferred from vendor country ({prefix}); symbol unreadable")
        inv.currency = currency

        order = self._date_order(values["issue_date"] + values["due_date"], currency)
        for field in ("issue_date", "due_date"):
            for text in values[field]:
                d = parse_date(text, order)
                if d:
                    setattr(inv, field, d)
                    break

        for field in ("subtotal", "tax_amount", "total"):
            for text in values[field]:
                if is_numeric_cell(text) and not parse_percent(text) and not parse_date(text):
                    amount = parse_amount(text)
                    if amount is not None:
                        setattr(inv, field, amount)
                        break
        if "tax_amount" in label_text:
            inv.tax_rate = parse_percent(label_text["tax_amount"])
            if inv.tax_rate is None:  # rate printed in its own cell next to the label
                for text in values["tax_amount"]:
                    if parse_percent(text):
                        inv.tax_rate = parse_percent(text)
                        break

        inv.line_items = self.line_items(self._decimal_sep())

        found = sum(getattr(inv, f) is not None for f in REQUIRED)
        confidence = found / len(REQUIRED) - 0.05 * len(inferred)
        if self.page.source == "ocr" and self.page.ocr_scores:
            confidence *= min(1.0, sum(self.page.ocr_scores) / len(self.page.ocr_scores) + 0.05)
        return ExtractionResult(invoice=inv, method=self.page.source, confidence=round(max(confidence, 0), 3),
                                raw_text=all_text, notes=self.notes)

    # --- line items ----------------------------------------------------------------------------------
    def _is_header(self, line: Line) -> bool:
        words = 0
        for c in line.cells:
            for part in re.split(r"[\s/]+", c.text):
                f = fold(part).strip(":")
                if f in HEADER_WORDS or f.rstrip(".") in HEADER_WORDS:
                    words += 1
            f_all = fold(c.text).strip(":")
            if f_all in HEADER_WORDS and " " in c.text:
                words += 1
        return words >= 3

    def line_items(self, decimal_sep: str) -> list[LineItem]:
        start = next((i for i, line in enumerate(self.lines) if self._is_header(line)), None)
        if start is None:
            return []
        ocr = self.page.source == "ocr"
        header = fold(self.lines[start].text)
        # a percent cell in a row is a discount only when the header says so; otherwise a VAT column
        has_discount = any(w in header for w in ("dto", "discount", "rabatt", "sconto", "remise", "korting", "desc."))
        items: list[LineItem] = []
        for line in self.lines[start + 1 :]:
            if any((m := _label_match(c.text)) and m[0] in ("subtotal", "total", "tax_amount") for c in line.cells):
                break
            rate_cells = [c for c in line.cells if RATE_CELL_RE.match(c.text.strip())]
            cells = [c for c in line.cells if c not in rate_cells]  # "21%" column is not description
            numeric = [c for c in cells if is_numeric_cell(c.text)]
            text_cells = [c for c in cells if not is_numeric_cell(c.text)]
            if not numeric:
                if text_cells and items:  # wrapped description
                    items[-1].description += " " + " ".join(c.text for c in text_cells)
                continue
            description = " ".join(c.text for c in text_cells).strip()
            amount = parse_amount(numeric[-1].text)
            unit = parse_amount(numeric[-2].text) if len(numeric) >= 2 else None
            qty = parse_number(_strip_currency(numeric[-3].text), decimal_sep) if len(numeric) >= 3 else None
            if amount is None or unit is None:
                break
            n = len(items) + 1
            if qty is None and ocr and unit:
                # OCR dropped the quantity cell: recover it only if amount / unit is a clean quantity
                q = (amount / unit).quantize(Decimal("0.01"))
                if q > 0 and money(q * unit) == amount and q == q.quantize(Decimal("0.25")):
                    qty = q.normalize()
                    self.notes.append(f"line {n}: quantity {qty} recovered from amount / unit price")
            if qty is None:
                break
            discount = parse_percent(rate_cells[0].text) if has_discount and rate_cells else None
            if ocr and discount is None and money(qty * unit) != amount:
                qty, unit, amount = self._repair_separators(n, qty, unit, amount, numeric)
            items.append(LineItem(description=description, quantity=qty, unit_price=unit, amount=amount,
                                  discount_percent=discount))
        return items

    def _repair_separators(self, n, qty, unit, amount, cells):
        """OCR sometimes drops a decimal separator ('158,43' -> '15843'). If dividing exactly one
        separator-less value by 100 makes quantity x unit = amount hold, take it and say so."""
        texts = {"unit": _strip_currency(cells[-2].text), "amount": _strip_currency(cells[-1].text)}
        for name in ("unit", "amount"):
            if re.search(r"[.,]\d{2}$", texts[name]):
                continue
            u = unit / 100 if name == "unit" else unit
            a = amount / 100 if name == "amount" else amount
            if money(qty * u) == money(a):
                self.notes.append(f"line {n}: {name} {texts[name]} read as {money(u if name == 'unit' else a)} "
                                  "(decimal separator lost in scan)")
                return qty, money(u), money(a)
        return qty, unit, amount


def extract(page: PageText) -> ExtractionResult:
    return Extractor(page).extract()
