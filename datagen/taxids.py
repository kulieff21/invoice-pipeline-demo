"""Tax ID generators with real check-digit schemes (DE, FR, GB). US EIN is format-only.

The same checks live in the pipeline (src/invoice_pipeline/taxid.py); the generator keeps its own
copy so a bug in one cannot silently agree with a bug in the other.
"""

from __future__ import annotations

import random


def de_check_digit(first8: str) -> int:
    # ISO 7064 MOD 11,10
    product = 10
    for ch in first8:
        total = (int(ch) + product) % 10
        if total == 0:
            total = 10
        product = (2 * total) % 11
    check = 11 - product
    return 0 if check == 10 else check


def make_de(rng: random.Random) -> str:
    first8 = str(rng.randint(1, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(7))
    return f"DE{first8}{de_check_digit(first8)}"


def make_fr(rng: random.Random) -> str:
    siren = rng.randint(100_000_000, 999_999_999)
    key = (12 + 3 * (siren % 97)) % 97
    return f"FR{key:02d}{siren}"


def make_gb(rng: random.Random) -> str:
    while True:
        d = [rng.randint(0, 9) for _ in range(7)]
        weighted = sum(w * x for w, x in zip(range(8, 1, -1), d))
        check = (-weighted) % 97
        if check < 100:
            digits = "".join(map(str, d)) + f"{check:02d}"
            return f"GB{digits}"


def make_us(rng: random.Random) -> str:
    return f"{rng.randint(10, 98):02d}-{rng.randint(0, 9_999_999):07d}"


def make_nl(rng: random.Random) -> str:
    return f"NL{rng.randint(0, 999_999_999):09d}B{rng.randint(1, 99):02d}"


def make_it(rng: random.Random) -> str:
    return f"IT{rng.randint(0, 99_999_999_999):011d}"


MAKERS = {"DE": make_de, "FR": make_fr, "GB": make_gb, "US": make_us, "NL": make_nl, "IT": make_it}
FORMAT_ONLY = ("NL", "IT")  # no check digit in the validator: a typo must break the format


def corrupt(tax_id: str, rng: random.Random) -> str:
    """One-digit typo that breaks the check digit (or the EIN length)."""
    if "-" in tax_id or tax_id.startswith(FORMAT_ONLY):  # drop a digit
        i = max(i for i, ch in enumerate(tax_id) if ch.isdigit())
        return tax_id[:i] + tax_id[i + 1 :]
    positions = [i for i, ch in enumerate(tax_id) if ch.isdigit()]
    while True:
        i = rng.choice(positions)
        new = str((int(tax_id[i]) + rng.randint(1, 9)) % 10)
        candidate = tax_id[:i] + new + tax_id[i + 1 :]
        if not is_valid(candidate):
            return candidate


def is_valid(tax_id: str) -> bool:
    if tax_id.startswith("DE") and len(tax_id) == 11 and tax_id[2:].isdigit():
        return de_check_digit(tax_id[2:10]) == int(tax_id[10])
    if tax_id.startswith("FR") and len(tax_id) == 13 and tax_id[2:].isdigit():
        return int(tax_id[2:4]) == (12 + 3 * (int(tax_id[4:]) % 97)) % 97
    if tax_id.startswith("GB") and len(tax_id) == 11 and tax_id[2:].isdigit():
        d = [int(c) for c in tax_id[2:9]]
        total = sum(w * x for w, x in zip(range(8, 1, -1), d)) + int(tax_id[9:11])
        return total % 97 == 0 or (total + 55) % 97 == 0
    if tax_id.startswith("NL"):
        return len(tax_id) == 14 and tax_id[2:11].isdigit() and tax_id[11] == "B" and tax_id[12:].isdigit()
    if tax_id.startswith("IT"):
        return len(tax_id) == 13 and tax_id[2:].isdigit()
    return len(tax_id) == 10 and tax_id[2] == "-" and tax_id.replace("-", "").isdigit()
