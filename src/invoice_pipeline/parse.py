"""Tolerant parsers for amounts, numbers and dates as they appear on invoices and in OCR output."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

CURRENCY_SYMBOLS = {"£": "GBP", "€": "EUR", "$": "USD", "¥": "JPY"}
CURRENCY_CODES = {"EUR", "USD", "GBP", "CHF", "PLN", "SEK", "DKK", "NOK", "CZK", "HUF", "CAD", "AUD", "AZN", "TRY", "JPY"}

MONTHS = {
    # en
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
    # de
    "januar": 1, "februar": 2, "marz": 3, "mai": 5, "juni": 6, "juli": 7, "okt": 10, "dez": 12,
    # fr
    "janv": 1, "fevr": 2, "mars": 3, "avr": 4, "juin": 6, "juil": 7, "aout": 8, "déc": 12,
    # it / es / nl
    "gen": 1, "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7,
    "ago": 8, "agosto": 8, "set": 9, "settembre": 9, "ott": 10, "ottobre": 10, "novembre": 11, "dic": 12,
    "dicembre": 12, "ene": 1, "enero": 1, "abr": 4, "mayo": 5, "junio": 6, "julio": 7, "septiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12, "mrt": 3, "maart": 3, "mei": 5, "okt.": 10,
}


def fold(text: str) -> str:
    """Lowercase, strip accents and whitespace: 'Fällig am' and OCR's 'Falligam' compare equal."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", "", text.lower())


AMOUNT_RE = re.compile(r"-?\d{1,3}(?:[.,'  ]\d{3})*(?:[.,]\d{1,2})?|-?\d+(?:[.,]\d{1,2})?")


def find_currency(text: str) -> str | None:
    for sym, code in CURRENCY_SYMBOLS.items():
        if sym in text:
            return code
    for token in re.findall(r"\b[A-Z]{3}\b", text):
        if token in CURRENCY_CODES:
            return token
    return None


def parse_amount(text: str) -> Decimal | None:
    """'£2,258.26', '3.156,55 €', '1 487,78 €', '3.202.08' (OCR) -> Decimal.

    The last '.' or ',' followed by exactly two digits at the end is the decimal separator;
    every other separator is a thousands separator. That rule also absorbs OCR swapping , and .
    """
    cleaned = re.sub(r"[^\d.,'\-\s ]", "", text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not re.search(r"\d", cleaned):
        return None
    negative = cleaned.startswith("-")
    digits = cleaned.lstrip("-").strip()
    m = re.search(r"[.,](\d{2})$", digits)
    if m:
        whole = re.sub(r"\D", "", digits[: m.start()])
        value = f"{whole or '0'}.{m.group(1)}"
    else:
        value = re.sub(r"\D", "", digits)
    try:
        result = Decimal(value)
    except InvalidOperation:
        return None
    return -result if negative else result


def parse_number(text: str, decimal_sep: str) -> Decimal | None:
    """Quantities and rates: '39,75', '2', '8.875'. The document's decimal separator decides."""
    token = re.search(r"-?\d+(?:[.,]\d+)?", text.replace(" ", ""))
    if not token:
        return None
    raw = token.group(0)
    other = "," if decimal_sep == "." else "."
    if other in raw and decimal_sep not in raw and len(raw.split(other)[1]) == 3 and decimal_sep == ",":
        raw = raw.replace(other, "")  # thousands separator in a quantity
    raw = raw.replace(",", ".")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


DATE_NUMERIC = re.compile(r"(\d{1,4})[./\-](\d{1,2})[./\-](\d{2,4})")
DATE_WORDY = re.compile(r"(\d{1,2})\.?\s*([A-Za-zÀ-ÿ]{3,10})\.?,?\s*(\d{4})")
DATE_WORDY_US = re.compile(r"([A-Za-z]{3,10})\.?\s+(\d{1,2}),?\s+(\d{4})")


def date_parts(text: str) -> tuple[int, int, int] | None:
    """Return the raw (a, b, year) of a numeric date, or None."""
    m = DATE_NUMERIC.search(text.replace(" ", ""))
    if not m:
        return None
    a, b, c = m.groups()
    if len(a) == 4:  # ISO yyyy-mm-dd
        return int(c), int(b), int(a)  # normalised to (day, month, year)
    year = int(c) + (2000 if len(c) == 2 else 0)
    return int(a), int(b), year


def parse_date(text: str, order: str = "dmy") -> date | None:
    """order: 'dmy' or 'mdy' for ambiguous numeric dates. ISO dates are always y-m-d."""
    compact = text.replace(" ", "")
    m = DATE_NUMERIC.search(compact)
    if m:
        a, b, c = m.groups()
        try:
            if len(a) == 4:
                return date(int(a), int(b), int(c))
            year = int(c) + (2000 if len(c) == 2 else 0)
            day, month = (int(b), int(a)) if order == "mdy" else (int(a), int(b))
            return date(year, month, day)
        except ValueError:
            return None
    for regex, groups in ((DATE_WORDY, (0, 1, 2)), (DATE_WORDY_US, (1, 0, 2))):
        m = regex.search(text)
        if m:
            parts = m.groups()
            day, month_word, year = parts[groups[0]], parts[groups[1]], parts[groups[2]]
            month = MONTHS.get(fold(month_word).rstrip("."))
            if month is None:
                month = MONTHS.get(fold(month_word)[:3])
            if month:
                try:
                    return date(int(year), month, int(day))
                except ValueError:
                    return None
    return None


def parse_percent(text: str) -> Decimal | None:
    m = re.search(r"(\d{1,2}(?:[.,]\d{1,3})?)\s*%", text)
    if not m:
        return None
    return Decimal(m.group(1).replace(",", "."))
