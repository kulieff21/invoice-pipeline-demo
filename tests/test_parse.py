from datetime import date
from decimal import Decimal

import pytest

from invoice_pipeline.parse import fold, parse_amount, parse_date, parse_number, parse_percent


@pytest.mark.parametrize("text, expected", [
    ("£2,258.26", "2258.26"),
    ("3.156,55 €", "3156.55"),
    ("1 487,78 €", "1487.78"),
    ("$3,416.06", "3416.06"),
    ("3.202.08", "3202.08"),      # OCR turned the decimal comma into a dot
    ("42.98", "42.98"),
    ("CHF 1'234.50", "1234.50"),
    ("-1.00", "-1.00"),
    ("1500", "1500"),
])
def test_amounts(text, expected):
    assert parse_amount(text) == Decimal(expected)


@pytest.mark.parametrize("text, order, expected", [
    ("07 Sep 2026", "dmy", date(2026, 9, 7)),
    ("09.08.2026", "dmy", date(2026, 8, 9)),
    ("08/04/2026", "mdy", date(2026, 8, 4)),
    ("08/04/2026", "dmy", date(2026, 4, 8)),
    ("2026-09-25", "mdy", date(2026, 9, 25)),   # ISO ignores the locale order
    ("25 settembre 2026", "dmy", date(2026, 9, 25)),
    ("Sep 7, 2026", "dmy", date(2026, 9, 7)),
    ("09Sep2026", "dmy", date(2026, 9, 9)),      # OCR dropped the spaces
    ("31/02/2026", "dmy", None),
])
def test_dates(text, order, expected):
    assert parse_date(text, order) == expected


def test_numbers_and_rates():
    assert parse_number("39,75", ",") == Decimal("39.75")
    assert parse_number("2", ".") == Decimal("2")
    assert parse_percent("Sales Tax (8.875%)") == Decimal("8.875")
    assert parse_percent("zzgl. MwSt. 19 %") == Decimal("19")
    assert parse_percent("TVA 5,5 %") == Decimal("5.5")


def test_fold_equates_ocr_and_print():
    assert fold("Fällig am:") == fold("Falligam:")
