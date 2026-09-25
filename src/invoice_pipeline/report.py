"""Run report for the person who handles the review queue: what went through, what needs a
human, and why, in the order they should look at it."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from invoice_pipeline.state import State


def _money(inv: dict) -> str:
    total = inv.get("total")
    return f"{total} {inv.get('currency') or '?'}" if total is not None else "?"


def write_report(state: State, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    files = state.files()
    status = Counter(f["status"] for f in files)
    method = Counter(f["method"] for f in files)
    review = [f for f in files if f["status"] == "needs_review"]
    dups = [f for f in files if f["status"] == "duplicate"]
    codes = Counter(i["code"] for f in review for i in f["issues"])

    lines = [
        "# Invoice pipeline run report",
        "",
        f"Files processed: **{len(files)}**. OK: **{status['ok']}**, needs review: **{status['needs_review']}**, "
        f"duplicates skipped: **{status['duplicate']}**.",
        f"Read from text layer: {method['text']}, OCR: {method['ocr']}, LLM fallback: {method.get('ocr+llm', 0)}.",
        "",
        "## Why invoices need review",
        "",
        "| Issue | Invoices |",
        "|---|---:|",
        *[f"| `{code}` | {n} |" for code, n in codes.most_common()],
        "",
        "## Review queue",
        "",
        "| File | Vendor | Invoice no. | Total | Issues | Notes |",
        "|---|---|---|---:|---|---|",
    ]
    for f in review:
        inv = f["invoice"]
        issues = "<br>".join(f"`{i['code']}` {i['message']}" for i in f["issues"])
        notes = "<br>".join(f["notes"])
        lines.append(f"| {f['name']} | {inv.get('vendor_name') or '?'} | {inv.get('invoice_number') or '?'} | "
                     f"{_money(inv)} | {issues} | {notes} |")
    if dups:
        lines += ["", "## Duplicates (no row written)", "", "| File | Note |", "|---|---|"]
        lines += [f"| {f['name']} | {'; '.join(f['notes'])} |" for f in dups]

    md = out / "run_report.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    queue = out / "review_queue.csv"
    with open(queue, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "vendor", "invoice_number", "total", "currency", "issue_codes", "issues", "notes"])
        for f in review:
            inv = f["invoice"]
            w.writerow([f["name"], inv.get("vendor_name"), inv.get("invoice_number"), inv.get("total"),
                        inv.get("currency"), " ".join(i["code"] for i in f["issues"]),
                        " | ".join(i["message"] for i in f["issues"]), " | ".join(f["notes"])])
    return [md, queue]
