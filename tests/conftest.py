import os
from pathlib import Path

import pytest

from datagen.generate import generate


@pytest.fixture(scope="session")
def text_inbox(tmp_path_factory) -> Path:
    """40 text-layer invoices (no scans, so no OCR wait) with planted defects and duplicates."""
    root = tmp_path_factory.mktemp("data")
    generate("t", 40, seed=11, layouts=["uk", "de", "us", "fr"], out_root=root,
             defect_rate=0.3, scan_rate=0.0, dup_rate=0.0)
    return root / "t"


@pytest.fixture(scope="session")
def dup_inbox(tmp_path_factory) -> Path:
    """Inbox with re-sent copies (scans), for duplicate handling. OCR runs here: slow."""
    root = tmp_path_factory.mktemp("dup")
    generate("d", 16, seed=5, layouts=["de", "us"], out_root=root, defect_rate=0.0, scan_rate=0.0, dup_rate=0.35)
    return root / "d"


OCR_CACHE = os.environ.get("INVOICE_OCR_CACHE")
