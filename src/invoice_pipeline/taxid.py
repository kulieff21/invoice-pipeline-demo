"""Vendor tax ID checks. DE, FR and GB use their published check-digit schemes; US EIN and other
countries are checked for format only (a well-formed ID can still be unregistered)."""

from __future__ import annotations

import re


def _de_ok(digits: str) -> bool:
    product = 10
    for d in map(int, digits[:8]):
        s = (d + product) % 10 or 10
        product = (2 * s) % 11
    return (11 - product) % 10 == int(digits[8])


def _fr_ok(key: str, siren: str) -> bool:
    return int(key) == (12 + 3 * (int(siren) % 97)) % 97


def _gb_ok(digits: str) -> bool:
    weights = [8, 7, 6, 5, 4, 3, 2]
    total = sum(w * int(d) for w, d in zip(weights, digits[:7])) + int(digits[7:9])
    return total % 97 in (0, 42)  # 42 == 97 - 55: the 2010+ number series


FORMATS = {
    "AT": r"U\d{8}", "BE": r"[01]\d{9}", "ES": r"[0-9A-Z]\d{7}[0-9A-Z]", "IT": r"\d{11}",
    "NL": r"\d{9}B\d{2}", "PL": r"\d{10}", "SE": r"\d{12}", "DK": r"\d{8}", "IE": r"\d{7}[A-Z]{1,2}",
}


def normalize(tax_id: str) -> str:
    return re.sub(r"[\s.]", "", tax_id).upper()


def check(tax_id: str) -> tuple[bool, str]:
    """(valid, how) where how is 'checksum', 'format' or 'unknown-country'."""
    t = normalize(tax_id)
    if re.fullmatch(r"\d{2}-\d{7}", t):
        return True, "format"
    country, body = t[:2], t[2:]
    if country == "DE":
        return bool(re.fullmatch(r"\d{9}", body)) and _de_ok(body), "checksum"
    if country == "FR":
        return bool(re.fullmatch(r"\d{11}", body)) and _fr_ok(body[:2], body[2:]), "checksum"
    if country == "GB":
        return bool(re.fullmatch(r"\d{9}", body)) and _gb_ok(body), "checksum"
    if country in FORMATS:
        return bool(re.fullmatch(FORMATS[country], body)), "format"
    return bool(re.fullmatch(r"[A-Z]{2}[0-9A-Z]{8,12}", t)), "unknown-country"
