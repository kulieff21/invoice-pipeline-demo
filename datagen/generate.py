"""Generate a synthetic invoice inbox with known ground truth.

Every document is fictional (Faker names and addresses, generated tax IDs). Some documents carry
exactly one planted defect; labels.jsonl records what is printed on each page and which issue
codes a correct validator must raise.

    uv run python -m datagen.generate --split dev --n 120 --seed 7
"""

from __future__ import annotations

import argparse
import io
import json
import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from faker import Faker
from PIL import Image, ImageFilter

from datagen import taxids
from datagen.holdout_layouts import RENDERERS as HOLDOUT_RENDERERS
from datagen.layouts import RENDERERS as DEV_RENDERERS
from datagen.layouts import Doc, Party
from invoice_pipeline.models import Invoice, LineItem, Status, money

RENDERERS = {**DEV_RENDERERS, **HOLDOUT_RENDERERS}
AS_OF = date(2026, 9, 25)  # the "today" the evaluation runs against

LOCALES = {
    "uk": dict(faker="en_GB", country="United Kingdom", tax="GB", currency=["GBP", "GBP", "EUR"], rates=["20"]),
    "de": dict(faker="de_DE", country="Deutschland", tax="DE", currency=["EUR"], rates=["19", "19", "7"]),
    "us": dict(faker="en_US", country="United States", tax="US", currency=["USD"], rates=["6", "7.25", "8.875", "6.35"]),
    "fr": dict(faker="fr_FR", country="France", tax="FR", currency=["EUR"], rates=["20", "20", "10", "5.5"]),
    # holdout
    "nl": dict(faker="nl_NL", country="Netherlands", tax="NL", currency=["EUR"], rates=["21", "21", "9"]),
    "it": dict(faker="it_IT", country="Italia", tax="IT", currency=["EUR"], rates=["22", "22", "10", "4"]),
}

CATALOG = {
    "en": [
        ("Website maintenance, monthly retainer", (1, 1), (350, 1200)),
        ("Consulting - data migration workshop", (4, 16), (85, 160)),
        ("USB-C docking station 12-in-1", (1, 8), (89, 240)),
        ("Laptop stand, aluminium", (2, 10), (29, 75)),
        ("Cloud hosting plan (Business, 3 vCPU / 8 GB)", (1, 3), (40, 180)),
        ("Copywriting: product descriptions for 25 SKUs", (1, 2), (300, 900)),
        ("On-site support visit incl. travel within zone 2", (1, 3), (120, 260)),
        ("Annual software licence - 5 seats", (1, 2), (400, 2500)),
        ("Printer toner cartridge, black, high yield", (2, 12), (38, 95)),
        ("Design review and accessibility audit of checkout flow", (1, 1), (600, 1800)),
        ("Hours - backend development (sprint 14)", (6, 40), (55, 120)),
        ("Shipping and handling", (1, 1), (9, 45)),
    ],
    "de": [
        ("Wartungspauschale Webseite, monatlich", (1, 1), (350, 1200)),
        ("Beratung Datenmigration, Workshop vor Ort", (4, 16), (85, 160)),
        ("USB-C Dockingstation 12-in-1", (1, 8), (89, 240)),
        ("Monitorhalterung, Aluminium, 2 Arme", (2, 10), (29, 75)),
        ("Cloud-Hosting Tarif Business (3 vCPU / 8 GB)", (1, 3), (40, 180)),
        ("Stunden Backend-Entwicklung (Sprint 14)", (6, 40), (55, 120)),
        ("Jahreslizenz Software, 5 Arbeitsplätze", (1, 2), (400, 2500)),
        ("Versandkosten", (1, 1), (9, 45)),
        ("Druckerpatrone schwarz, XL", (2, 12), (38, 95)),
    ],
    "fr": [
        ("Maintenance site web, forfait mensuel", (1, 1), (350, 1200)),
        ("Conseil migration de données, atelier sur site", (4, 16), (85, 160)),
        ("Station d'accueil USB-C 12-en-1", (1, 8), (89, 240)),
        ("Hébergement cloud offre Business (3 vCPU / 8 Go)", (1, 3), (40, 180)),
        ("Heures de développement backend (sprint 14)", (6, 40), (55, 120)),
        ("Licence logicielle annuelle, 5 postes", (1, 2), (400, 2500)),
        ("Frais de port", (1, 1), (9, 45)),
        ("Cartouche toner noir, haute capacité", (2, 12), (38, 95)),
    ],
}
CATALOG["it"] = [
    ("Canone manutenzione sito web, mensile", (1, 1), (350, 1200)),
    ("Consulenza migrazione dati, workshop in sede", (4, 16), (85, 160)),
    ("Docking station USB-C 12-in-1", (1, 8), (89, 240)),
    ("Hosting cloud piano Business (3 vCPU / 8 GB)", (1, 3), (40, 180)),
    ("Ore di sviluppo backend (sprint 14)", (6, 40), (55, 120)),
    ("Licenza software annuale, 5 postazioni", (1, 2), (400, 2500)),
    ("Spese di spedizione", (1, 1), (9, 45)),
    ("Toner stampante nero, alta capacità", (2, 12), (38, 95)),
]
CATALOG_BY_LAYOUT = {"uk": "en", "us": "en", "de": "de", "fr": "fr", "nl": "en", "it": "it"}

NUMBER_STYLES = [
    lambda n: f"INV-2026-{n:04d}",
    lambda n: f"RE-2026-{n:04d}",
    lambda n: f"{n + 10000}",
    lambda n: f"F2026-{n:04d}",
    lambda n: f"26/{n:05d}",
]

# planted defect -> expected issue code
DEFECTS = {
    "line_amount": "LINE_AMOUNT_MISMATCH",
    "tax": "TAX_MISMATCH",
    "total": "TOTAL_MISMATCH",
    "due_before_issue": "DUE_BEFORE_ISSUE",
    "missing_number": "MISSING_INVOICE_NUMBER",
    "tax_id": "INVALID_TAX_ID",
    "future_date": "FUTURE_DATE",
}


def _one_line(s: str) -> str:
    return ", ".join(part.strip() for part in s.splitlines())


class Vendor:
    def __init__(self, layout: str, rng: random.Random, fake: Faker):
        loc = LOCALES[layout]
        self.layout = layout
        self.party = Party(fake.company(), _one_line(fake.street_address()), f"{fake.postcode()} {fake.city()}", loc["country"])
        if layout == "us":
            self.party.city = f"{fake.city()}, {fake.state_abbr()} {fake.postcode()}"
        self.tax_id = taxids.MAKERS[loc["tax"]](rng)
        self.number_style = rng.choice(NUMBER_STYLES)
        self.next_number = rng.randint(1, 900)
        self.currency = rng.choice(loc["currency"])
        self.terms = rng.choice([14, 30, 30, 45])


def make_doc(vendor: Vendor, rng: random.Random, fake: Faker) -> Doc:
    loc = LOCALES[vendor.layout]
    catalog = CATALOG[CATALOG_BY_LAYOUT[vendor.layout]]
    lines = []
    for desc, (qlo, qhi), (plo, phi) in rng.sample(catalog, rng.randint(1, 6)):
        if any(w in desc for w in ("Hours", "Stunden", "Heures", "Ore di")):
            qty = Decimal(rng.randint(qlo * 4, qhi * 4)) / 4  # quarter hours
        else:
            qty = Decimal(rng.randint(qlo, qhi))
        unit = money(Decimal(rng.randint(plo * 100, phi * 100)) / 100)
        lines.append((desc, qty, unit, money(qty * unit)))
    subtotal = money(sum(line[3] for line in lines))
    rate = Decimal(rng.choice(loc["rates"]))
    tax = money(subtotal * rate / 100)
    issue = date(2026, 6, 1) + timedelta(days=rng.randint(0, 111))  # up to 2026-09-20
    number = vendor.number_style(vendor.next_number)
    vendor.next_number += rng.randint(1, 30)
    customer = Party(fake.company(), _one_line(fake.street_address()), f"{fake.postcode()} {fake.city()}", loc["country"])
    return Doc(
        layout=vendor.layout,
        vendor=vendor.party,
        customer=customer,
        tax_id=vendor.tax_id,
        number=number,
        issue=issue,
        due=issue + timedelta(days=vendor.terms),
        currency=vendor.currency,
        lines=lines,
        subtotal=subtotal,
        tax_rate=rate,
        tax=tax,
        total=subtotal + tax,
    )


def plant(doc: Doc, defect: str, rng: random.Random) -> None:
    """Apply one defect; everything not named by the defect stays arithmetically consistent."""
    if defect == "line_amount":
        i = rng.randrange(len(doc.lines))
        desc, qty, unit, amount = doc.lines[i]
        doc.lines[i] = (desc, qty, unit, amount + money(rng.choice([10, 1, 100, 0.9])))
        doc.subtotal = money(sum(line[3] for line in doc.lines))
        doc.tax = money(doc.subtotal * doc.tax_rate / 100)
        doc.total = doc.subtotal + doc.tax
    elif defect == "tax":
        wrong = doc.tax_rate - Decimal(rng.choice([1, 2, 3]))
        doc.tax = money(doc.subtotal * wrong / 100)
        doc.total = doc.subtotal + doc.tax
    elif defect == "total":
        doc.total = doc.total + money(rng.choice([10, 100, 0.1, -1]))
    elif defect == "due_before_issue":
        doc.due = doc.issue - timedelta(days=rng.randint(3, 20))
    elif defect == "missing_number":
        doc.number = None
    elif defect == "tax_id":
        doc.tax_id = taxids.corrupt(doc.tax_id, rng)
    elif defect == "future_date":
        doc.issue = AS_OF + timedelta(days=rng.randint(20, 70))
        doc.due = doc.issue + timedelta(days=30)
    else:
        raise ValueError(defect)


def to_invoice(doc: Doc) -> Invoice:
    return Invoice(
        vendor_name=doc.vendor.name,
        vendor_tax_id=doc.tax_id,
        invoice_number=doc.number,
        issue_date=doc.issue,
        due_date=doc.due,
        currency=doc.currency,
        subtotal=doc.subtotal,
        tax_rate=doc.tax_rate,
        tax_amount=doc.tax,
        total=doc.total,
        line_items=[LineItem(description=d, quantity=q, unit_price=u, amount=a) for d, q, u, a in doc.lines],
    )


def scan(pdf_bytes: bytes, rng: random.Random) -> bytes:
    """Rasterise and degrade like an office scanner: 150 dpi, slight skew, noise, blur, JPEG."""
    page = pdfium.PdfDocument(pdf_bytes)[0]
    img = page.render(scale=150 / 72).to_pil().convert("L")
    img = img.rotate(rng.uniform(-1.2, 1.2), resample=Image.BICUBIC, expand=True, fillcolor=255)
    arr = np.asarray(img, dtype=np.float32)
    noise = np.random.default_rng(rng.randint(0, 2**32 - 1)).normal(0, 9, arr.shape)
    arr = np.clip(arr + noise - 6, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr).filter(ImageFilter.GaussianBlur(0.5))
    jpg = io.BytesIO()
    img.save(jpg, "JPEG", quality=55)
    out = io.BytesIO()
    Image.open(jpg).save(out, "PDF", resolution=150)
    return out.getvalue()


def generate(split: str, n: int, seed: int, layouts: list[str], out_root: Path,
             defect_rate: float = 0.25, scan_rate: float = 0.25, dup_rate: float = 0.05) -> None:
    rng = random.Random(seed)
    Faker.seed(seed)
    fakers = {k: Faker(v["faker"]) for k, v in LOCALES.items()}
    vendors = [Vendor(layout, rng, fakers[layout]) for layout in layouts for _ in range(6)]

    out = out_root / split
    (out / "inbox").mkdir(parents=True, exist_ok=True)
    for old in (out / "inbox").glob("*.pdf"):
        old.unlink()

    defect_names = list(DEFECTS)
    records, emitted = [], []
    index = 0
    for _ in range(n):
        vendor = rng.choice(vendors)
        doc = make_doc(vendor, rng, fakers[vendor.layout])
        defect = rng.choice(defect_names) if rng.random() < defect_rate else None
        if defect:
            plant(doc, defect, rng)
        pdf = RENDERERS[doc.layout](doc)
        scanned = rng.random() < scan_rate
        if scanned:
            pdf = scan(pdf, rng)
        index += 1
        name = f"inv_{index:04d}.pdf"
        (out / "inbox" / name).write_bytes(pdf)
        record = {
            "file": name,
            "layout": doc.layout,
            "scanned": scanned,
            "defect": defect,
            "expected_issues": [DEFECTS[defect]] if defect else [],
            "expected_status": Status.NEEDS_REVIEW if defect else Status.OK,
            "invoice": json.loads(to_invoice(doc).model_dump_json()),
        }
        records.append(record)
        emitted.append((doc, record))

        # Re-sent copy of an earlier clean invoice: same content, different file (usually a scan).
        if rng.random() < dup_rate and len(emitted) > 3:
            src_doc, src_rec = rng.choice([e for e in emitted[:-1] if e[1]["defect"] is None] or [emitted[0]])
            index += 1
            dup_name = f"inv_{index:04d}.pdf"
            (out / "inbox" / dup_name).write_bytes(scan(RENDERERS[src_doc.layout](src_doc), rng))
            records.append({
                **src_rec,
                "file": dup_name,
                "scanned": True,
                "defect": "duplicate",
                "duplicate_of": src_rec["file"],
                "expected_issues": ["DUPLICATE_INVOICE"],
                "expected_status": Status.DUPLICATE,
            })

    with open(out / "labels.jsonl", "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    counts = {}
    for r in records:
        counts[r["defect"] or "clean"] = counts.get(r["defect"] or "clean", 0) + 1
    print(f"{split}: {len(records)} files -> {out}  {dict(sorted(counts.items()))}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--layouts", default="uk,de,us,fr")
    ap.add_argument("--out", default="data")
    args = ap.parse_args()
    generate(args.split, args.n, args.seed, args.layouts.split(","), Path(args.out))


if __name__ == "__main__":
    main()
