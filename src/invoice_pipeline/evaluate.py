"""Score extraction and validation against generator labels.

    uv run python -m invoice_pipeline.evaluate data/dev --out results/dev.json

Field accuracy is exact match after normalisation (case, whitespace, accents for names; money
to the cent). Issue detection is scored per planted defect: a defect counts as caught when its
code is raised; any other code raised on that file is a false alarm.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from invoice_pipeline.layout import read_page
from invoice_pipeline.models import FIELDS, Invoice
from invoice_pipeline.parse import fold
from invoice_pipeline.rules import extract
from invoice_pipeline.taxid import normalize as norm_tax
from invoice_pipeline.validate import validate

AS_OF = date(2026, 9, 25)


def _norm(field: str, value):
    if value is None:
        return None
    if field == "vendor_name":
        return re.sub(r"[^0-9a-z]", "", fold(str(value)))
    if field == "vendor_tax_id":
        return norm_tax(str(value))
    if field == "invoice_number":
        return re.sub(r"\s", "", str(value)).upper()
    if field in ("subtotal", "tax_amount", "total", "tax_rate"):
        return Decimal(str(value)).normalize()
    return str(value)


def compare(truth: Invoice, got: Invoice) -> dict[str, bool]:
    out = {f: _norm(f, getattr(truth, f)) == _norm(f, getattr(got, f)) for f in FIELDS}
    tl, gl = truth.line_items, got.line_items
    out["line_items"] = len(tl) == len(gl) and all(
        a.quantity == b.quantity and a.unit_price == b.unit_price and a.amount == b.amount for a, b in zip(tl, gl)
    )
    out["line_descriptions"] = len(tl) == len(gl) and all(
        re.sub(r"[^0-9a-z]", "", fold(a.description)) == re.sub(r"[^0-9a-z]", "", fold(b.description))
        for a, b in zip(tl, gl)
    )
    return out


def run(split_dir: Path, cache_dir: str | None, fallback=None) -> dict:
    labels = [json.loads(line) for line in open(split_dir / "labels.jsonl", encoding="utf-8")]
    per_field: dict[str, Counter] = defaultdict(Counter)
    defects = Counter()
    caught = Counter()
    false_alarms = Counter()
    clean_flagged = 0
    slipped: list[str] = []
    silent: list[dict] = []  # planted defect, yet no issue at all: would reach the sheet as "ok"
    clean_total = 0
    rows = []
    t0 = time.time()
    for rec in labels:
        if rec["defect"] == "duplicate":
            continue  # duplicates are a pipeline (state) concern, scored in the pipeline test
        path = split_dir / "inbox" / rec["file"]
        page = read_page(str(path), cache_dir)
        result = extract(page)
        used_llm = False
        if fallback is not None:
            from invoice_pipeline.pipeline import needs_fallback

            if needs_fallback(result, validate(result.invoice, AS_OF, result.confidence)):
                from invoice_pipeline.reconcile import reconcile

                second = fallback(str(path), result)
                if second is not None:
                    result, used_llm = reconcile(result, second), True
        truth = Invoice.model_validate(rec["invoice"])
        cmp = compare(truth, result.invoice)
        kind = "scan" if rec["scanned"] else "text"
        for field, ok in cmp.items():
            per_field[field][f"{kind}_ok"] += ok
            per_field[field][f"{kind}_n"] += 1
        codes = sorted({i.code for i in validate(result.invoice, AS_OF, result.confidence,
                                                 conflicts=result.conflicts)})
        expected = set(rec["expected_issues"])
        if rec["defect"]:
            defects[rec["defect"]] += 1
            caught[rec["defect"]] += bool(expected & set(codes))
            if not codes:
                slipped.append(rec["file"])
        else:
            clean_total += 1
            clean_flagged += bool(codes)
        for code in set(codes) - expected:
            false_alarms[code] += 1
        wrong = [f for f, ok in cmp.items() if not ok and f != "line_descriptions"]
        if not codes and wrong:  # went to the sheet as "ok" with a misread value
            silent.append({"file": rec["file"], "fields": wrong})
        rows.append({
            "file": rec["file"], "layout": rec["layout"], "scanned": rec["scanned"], "defect": rec["defect"],
            "method": result.method, "llm": used_llm, "confidence": result.confidence, "codes": codes,
            "wrong_fields": [f for f, ok in cmp.items() if not ok],
            "got": json.loads(result.invoice.model_dump_json()),
        })
    summary = {
        "files": len(rows),
        "seconds": round(time.time() - t0, 1),
        "fields": {
            f: {k: v for k, v in c.items()} for f, c in per_field.items()
        },
        "defects": {d: {"planted": defects[d], "caught": caught[d]} for d in sorted(defects)},
        "clean": {"total": clean_total, "flagged": clean_flagged},
        "defective_passed_as_ok": slipped,
        "ok_with_wrong_fields": silent,
        "false_alarm_codes": dict(false_alarms.most_common()),
    }
    return {"summary": summary, "rows": rows}


def print_summary(s: dict) -> None:
    print(f"files {s['files']}  ({s['seconds']} s)")
    print(f"{'field':20} {'text':>12} {'scan':>12}")
    for f, c in s["fields"].items():
        cells = []
        for kind in ("text", "scan"):
            n = c.get(f"{kind}_n", 0)
            cells.append(f"{c.get(f'{kind}_ok', 0)}/{n}" if n else "-")
        print(f"{f:20} {cells[0]:>12} {cells[1]:>12}")
    print("defects caught:", {d: f"{v['caught']}/{v['planted']}" for d, v in s["defects"].items()})
    print(f"clean invoices flagged: {s['clean']['flagged']}/{s['clean']['total']}")
    print(f"defective invoices passed as ok: {len(s['defective_passed_as_ok'])} {s['defective_passed_as_ok']}")
    print(f"ok rows with a misread field: {len(s['ok_with_wrong_fields'])} {s['ok_with_wrong_fields']}")
    print("false alarm codes:", s["false_alarm_codes"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("split_dir", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--ocr-cache", default=None)
    ap.add_argument("--llm", action="store_true", help="apply the LLM fallback where the pipeline would")
    args = ap.parse_args()
    fallback = None
    if args.llm:
        from invoice_pipeline.llm import LLMFallback

        fallback = LLMFallback()
    report = run(args.split_dir, args.ocr_cache, fallback)
    if fallback is not None:
        report["summary"]["llm"] = {"model": fallback.model, **fallback.usage}
        print("llm:", report["summary"]["llm"])
    print_summary(report["summary"])
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
