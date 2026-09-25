"""Build the case-study page (site/index.html) from real runs. No number or example is typed by hand.

    uv run python tools/case_study.py

Sources
- results/*.json                  accuracy, holdout rounds, LLM runs, live-sheet run
- results/chaos-transcript.txt    output of `pytest -s tests/test_chaos.py`
- state/live2.sqlite              the run written to the live Google Sheet (hero rows, sheet table)
- data/*/                         invoice PDFs: thumbnails and the hero pages
- git history                     commits and tags of the holdout procedure
- tools/screenshots/*.png         the live Google Sheet, screenshots taken by hand
Hero boxes are the page cells the extraction rules used, located again on the rendered page.
"""

from __future__ import annotations

import html
import json
import os
import re
import sqlite3
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_pipeline.layout import _ocr_engine, from_text_layer, has_text_layer, read_page  # noqa: E402
from invoice_pipeline.parse import fold, parse_amount, parse_date  # noqa: E402
from invoice_pipeline.rules import Extractor  # noqa: E402

SITE = ROOT / "site"
ASSETS = SITE / "assets"
RES = ROOT / "results"
OCR_CACHE = os.environ.get("INVOICE_OCR_CACHE")  # optional: reuse OCR results between builds
LIVE_STATE = ROOT / "state" / "live2.sqlite"


def load(name: str) -> dict:
    data = json.loads((RES / name).read_text(encoding="utf-8"))
    return data.get("summary", data)


def passed_as_ok(s: dict) -> int:
    if "defective_passed_as_ok" in s:
        return len(s["defective_passed_as_ok"])
    caught = sum(v["caught"] for v in s["defects"].values())
    planted = sum(v["planted"] for v in s["defects"].values())
    if caught != planted:  # a caught defect has an issue, so it cannot have passed
        raise SystemExit("cannot derive defective_passed_as_ok")
    return 0


def defects(s: dict) -> tuple[int, int]:
    return (sum(v["caught"] for v in s["defects"].values()), sum(v["planted"] for v in s["defects"].values()))


def clean(s: dict) -> tuple[int, int]:
    return s["clean"]["flagged"], s["clean"]["total"]


def test_count() -> int:
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=ROOT, capture_output=True,
                         text=True).stdout
    m = re.search(r"(\d+) tests? collected", out)
    if not m:
        raise SystemExit("pytest collection failed:\n" + out[-2000:])
    return int(m.group(1))


def esc(s) -> str:
    return html.escape(str(s), quote=True)


# --- hero: real pages, real cells ------------------------------------------------------------------------
HERO = [
    ("dev", "inv_0002.pdf", "Clean invoice", "A German invoice read from its text layer. Every check passes."),
    ("dev", "inv_0011.pdf", "The vendor's mistake", "The line says 2 × 718.82 but prints 1537.64. Reported, not corrected."),
    ("dev", "inv_0053.pdf", "Scan + second reader", "OCR cannot read the € sign. The LLM reads it; the other fields agree."),
    ("dev", "inv_0108.pdf", "Readers disagree", "OCR and LLM read two different invoice numbers. Nothing decides: a person does."),
]
ROW_FIELDS = [("vendor_name", "vendor"), ("vendor_tax_id", "tax ID"), ("invoice_number", "number"),
              ("issue_date", "date"), ("currency", "cur."), ("total", "total")]


def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", fold(s))


def _matches(field: str, text: str, value) -> bool:
    if value is None:
        return False
    if field == "vendor_name":
        return _norm(text) == _norm(str(value))
    if field in ("vendor_tax_id", "invoice_number"):
        t = re.sub(r"[\s.]", "", text).upper().replace("CHE-", "CHE")
        return re.sub(r"[\s.]", "", str(value)).upper() in t
    if field in ("issue_date", "due_date"):
        return any(parse_date(text, o) is not None and str(parse_date(text, o)) == str(value) for o in ("dmy", "mdy"))
    if field in ("subtotal", "tax_amount", "total"):
        a = parse_amount(text)
        return a is not None and a == Decimal(str(value))
    if field == "currency":
        return {"EUR": "€", "GBP": "£", "USD": "$"}.get(value, value) in text or value in text
    return False


def hero_scenario(split: str, name: str, title: str, caption: str, live: dict) -> dict:
    path = ROOT / "data" / split / "inbox" / name
    rec = live[name]
    final = rec["invoice"]
    text_layer = has_text_layer(str(path))
    page_text = from_text_layer(str(path)) if text_layer else read_page(str(path), OCR_CACHE)
    ex = Extractor(page_text)

    pdf_page = pdfium.PdfDocument(str(path))[0]
    scale = 2.0 if text_layer else 150 / 72
    img = pdf_page.render(scale=scale).to_pil().convert("RGB")
    W, H = img.size

    # cell -> box on the rendered image. Text layer: points x scale. Scan: the raw OCR boxes, which
    # are in the skewed image frame (the pipeline deskews its copy for reading, the picture is not).
    raw = []
    if not text_layer:
        result, _ = _ocr_engine()(np.asarray(pdf_page.render(scale=150 / 72).to_pil().convert("RGB")))
        raw = [(t, b) for b, t, _ in (result or [])]

    def box(cell):
        if text_layer:
            return [cell.x0 * scale, cell.top * scale, (cell.x1 - cell.x0) * scale, (cell.bottom - cell.top) * scale]
        for t, b in raw:
            if t == cell.text:
                xs, ys = [p[0] for p in b], [p[1] for p in b]
                return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
        return None

    # crop the empty lower part of the page: the demo is about the printed area
    if text_layer:
        bottom = max(c.bottom for ln in ex.lines for c in ln.cells if c.top < page_text.height * 0.8) * scale
    else:
        bottom = max(max(p[1] for p in b) for t, b in raw if min(p[1] for p in b) < H * 0.8)
    H = min(H, int(bottom + 0.06 * W))
    img = img.crop((0, 0, W, H))

    def pct(b):
        return [round(100 * b[0] / W, 2), round(100 * b[1] / H, 2), round(100 * b[2] / W, 2), round(100 * b[3] / H, 2)]

    hits = ex._label_hits()
    marks = []
    llm_read = []
    for field, label in ROW_FIELDS:
        value = final.get(field)
        found = None
        for li, ci, _text, _lab in hits.get(field, []):
            line = ex.lines[li]
            cands = [line.cells[ci]] + line.cells[ci + 1:]
            if li + 1 < len(ex.lines):
                cands += [c for c in ex.lines[li + 1].cells if c.x0 < line.cells[ci].x1 + 5 and c.x1 > line.cells[ci].x0 - 5]
            for c in cands:
                if _matches(field, c.text, value):
                    found = (line.cells[ci], c)
                    break
            if found:
                break
        if not found:
            # no labelled cell: search the page; the currency is taken from the bottom (the total),
            # not from the first unit price
            lines = reversed(ex.lines) if field == "currency" else ex.lines
            for line in lines:
                for c in line.cells:
                    if _matches(field, c.text, value):
                        found = (None, c)
                        break
                if found:
                    break
        if not found:
            llm_read.append(field)
            continue
        lab, val = found
        vb = box(val)
        if vb is None:
            llm_read.append(field)
            continue
        m = {"field": field, "label": label, "value": pct(vb)}
        if lab is not None and lab is not val and box(lab):
            m["key"] = pct(box(lab))
        marks.append(m)

    # line table region: from the header row to the last line item
    header_i = next((i for i, ln in enumerate(ex.lines) if ex._is_header(ln)), None)
    if header_i is not None:
        items = ex.line_items(ex._decimal_sep())
        last = header_i + 1
        for i in range(header_i + 1, len(ex.lines)):
            if any(ex.lines[i].cells) and ex.lines[i].top > ex.lines[header_i].top:
                txt = ex.lines[i].text
                if any(k in fold(txt) for k in ("subtotal", "nettobetrag", "totalht", "imponibile")):
                    break
                last = i
        cells = [c for ln in ex.lines[header_i:last + 1] for c in ln.cells]
        bs = [b for b in (box(c) for c in cells) if b]
        if bs:
            x0 = min(b[0] for b in bs); y0 = min(b[1] for b in bs)
            x1 = max(b[0] + b[2] for b in bs); y1 = max(b[1] + b[3] for b in bs)
            marks.append({"field": "line_items", "label": f"{len(items)} lines", "value": pct([x0, y0, x1 - x0, y1 - y0])})

    out_name = f"hero-{name.replace('.pdf', '')}.webp"
    img.save(ASSETS / out_name, "WEBP", quality=84, method=6)
    row = {label: (str(final.get(field)) if final.get(field) is not None else "") for field, label in ROW_FIELDS}
    return {
        "title": title, "caption": caption, "file": name, "img": f"assets/{out_name}", "w": W, "h": H,
        "source": "text layer" if text_layer else "scan · OCR" + (" + LLM" if rec["method"] == "ocr+llm" else ""),
        "marks": marks, "llm": [dict(ROW_FIELDS)[f] for f in llm_read if final.get(f) is not None],
        "row": row, "status": rec["status"],
        "issues": [f"{i['code']}: {i['message']}" for i in rec["issues"]],
        "notes": [n for n in rec["notes"] if not n.startswith("fields read by")],
    }


def diff_table(by_name: dict) -> str:
    """Disagreements between the two readers in the live run, and what decided them."""
    from invoice_pipeline import taxid

    rows = []
    for name, rec in sorted(by_name.items()):
        for n in rec["notes"]:
            m = re.match(r"tax ID: readers disagree \((\S+) / (\S+)\)", n)
            if m:
                a, b = m.groups()
                kept, dropped = (a, b) if taxid.check(a)[0] else (b, a)
                rows.append((name, "tax ID", a, b, f'check digit: <span class="keep">kept {esc(kept)}</span>'))
        for i in rec["issues"]:
            m = re.match(r"(\w+): OCR read '(.*)', LLM read '(.*)'", i["message"]) if i["code"] == "READERS_DISAGREE" else None
            if m:
                rows.append((name, m.group(1).replace("_", " "), m.group(2), m.group(3),
                             '<span class="held">nothing decides</span> → review'))
    body = "".join(f"<tr><td>{esc(f)}</td><td>{esc(fl)}</td><td>{esc(a)}</td><td>{esc(b)}</td><td class=\"dec\">{d}</td></tr>"
                   for f, fl, a, b, d in rows)
    return ('<table class="diff"><thead><tr><th>file</th><th>field</th><th>OCR</th><th>LLM</th><th>decided by</th></tr></thead>'
            f"<tbody>{body}</tbody></table>")


def live_rows() -> tuple[dict, list[dict]]:
    db = sqlite3.connect(LIVE_STATE)
    by_name, table = {}, []
    for name, status, method, issues, notes, inv in db.execute(
            "SELECT name, status, method, issues, notes, invoice FROM files ORDER BY name"):
        rec = {"status": status, "method": method, "issues": json.loads(issues), "notes": json.loads(notes),
               "invoice": json.loads(inv)}
        by_name[name] = rec
        if status == "duplicate":
            continue
        i = rec["invoice"]
        table.append({"f": name, "s": status, "v": i.get("vendor_name") or "", "n": i.get("invoice_number") or "",
                      "d": i.get("issue_date") or "", "t": i.get("total") or "", "c": i.get("currency") or "",
                      "m": method, "i": " ".join(sorted({x["code"] for x in rec["issues"]}))})
    return by_name, table


# --- the rest ----------------------------------------------------------------------------------------------
SAMPLES = [
    ("dev", "uk", False, "UK", "dev"), ("dev", "de", False, "Germany", "dev"), ("dev", "us", False, "US", "dev"),
    ("dev", "fr", False, "France", "dev"), ("dev", "de", True, "Scan, 150 dpi", "dev"),
    ("holdout", "nl", False, "Netherlands", "holdout 1"), ("holdout", "it", False, "Italy", "holdout 1"),
    ("holdout2", "es", False, "Spain", "holdout 2"), ("holdout2", "ch", False, "Switzerland", "holdout 2"),
]


def contact_sheet() -> str:
    out = []
    for k, (split, layout, scanned, caption, group) in enumerate(SAMPLES):
        labels = [json.loads(line) for line in open(ROOT / "data" / split / "labels.jsonl", encoding="utf-8")]
        rec = next(r for r in labels if r["layout"] == layout and r["scanned"] == scanned and r["defect"] is None
                   and len(r["invoice"]["line_items"]) >= 3)
        page = pdfium.PdfDocument(str(ROOT / "data" / split / "inbox" / rec["file"]))[0]
        full = page.render(scale=1.6).to_pil().convert("RGB")
        base = f"invoice-{split}-{layout}{'-scan' if scanned else ''}"
        full.save(ASSETS / f"{base}-full.webp", "WEBP", quality=80, method=6)
        thumb = full.crop((0, 0, full.width, int(full.height * 0.5)))
        thumb.save(ASSETS / f"{base}.webp", "WEBP", quality=80, method=6)
        tilt = [-1.6, 1.1, -0.7, 1.4, -1.2, 0.8, -1.5, 1.2, -0.6][k]
        out.append(
            f'<button class="print" style="--tilt:{tilt}deg" data-full="assets/{base}-full.webp" '
            f'aria-label="Open synthetic invoice: {esc(caption)}">'
            f'<img src="assets/{base}.webp" width="{thumb.width}" height="{thumb.height}" loading="lazy" alt="">'
            f'<span class="print-cap"><span class="set set-{group.replace(" ", "")}">{esc(group)}</span>{esc(caption)}</span></button>')
    return "".join(out)


def screenshots() -> list[dict]:
    """Rows 1-21 only: row 22 shows a message wording that was corrected after the screenshot."""
    shots = []
    for name, label in (("sheet-keys", "keys & status"), ("sheet-issues", "amounts & issues"), ("sheet-notes", "notes")):
        img = Image.open(ROOT / "tools" / "screenshots" / f"{name}.png").convert("RGB")
        img = img.crop((0, 0, img.width, min(img.height, 584)))
        img.save(ASSETS / f"{name}.webp", "WEBP", quality=88, method=6)
        shots.append({"src": f"assets/{name}.webp", "label": label, "w": img.width, "h": img.height})
    return shots


def git_log() -> list[dict]:
    out = subprocess.run(["git", "log", "--reverse", "--format=%h\t%s\t%D"], cwd=ROOT, capture_output=True,
                         text=True).stdout
    commits = []
    for line in out.splitlines():
        h, subject, refs = (line.split("\t") + ["", ""])[:3]
        tag = re.search(r"tag: ([\w-]+)", refs)
        commits.append({"h": h, "s": subject, "tag": tag.group(1) if tag else ""})
    return commits


def timeline(commits: list[dict], h1f: dict, h2f: dict, h1: dict, h2: dict) -> str:
    def find(prefix):
        return next(c for c in commits if c["s"].startswith(prefix))

    def frac(t):
        return f"{t[0]}/{t[1]}"

    steps = [
        (find("Add LLM fallback"), "freeze", "Rules frozen. Nothing below may change them without saying so.", None),
        (find("Add holdout layouts (nl, it)"), "write", "Holdout 1 written: Dutch and Italian layouts the rules have never seen.", None),
        (find("Record holdout-1"), "measure", "First sight.", (clean(h1f), passed_as_ok(h1f), "bad")),
        (find("Rules v2"), "freeze", "Three general fixes, frozen again. Holdout 1 counts as seen from here.", (clean(h1), None, "mid")),
        (find("Add holdout-2 layouts"), "write", "Holdout 2 written: Spanish and Swiss layouts.", None),
        (find("Record holdout-2"), "measure", "First sight.", (clean(h2f), passed_as_ok(h2f), "bad")),
        (find("Rules v3"), "fix", "Fixes for what broke. Holdout 2 counts as seen from here.", (clean(h2), None, "mid")),
    ]
    rows = []
    for c, kind, note, res in steps:
        tag = f'<span class="ref">tag: {esc(c["tag"])}</span>' if c["tag"] else ""
        result = ""
        if res:
            (n, d), passed, tone = res
            result = (f'<div class="gl-res"><span class="gl-bar"><i class="fill {tone}" data-w="{100 * n / d:.1f}"></i></span>'
                      f'<span class="mono">{frac((n, d))} clean invoices to review</span>'
                      + (f'<span class="mono ok">· {passed} defective passed as ok</span>' if passed is not None else "")
                      + "</div>")
        rows.append(f'<li class="gl gl-{kind} reveal"><span class="dot"></span><div><span class="hash">{c["h"]}</span>{tag}'
                    f'<span class="msg">{esc(c["s"])}</span><span class="note">{esc(note)}</span>{result}</div></li>')
    return "".join(rows)


def render(values: dict) -> str:
    template = (ROOT / "tools" / "case_template.html").read_text(encoding="utf-8")

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise KeyError(f"template placeholder {{{{{key}}}}} has no value")
        return str(values[key])

    return re.sub(r"\{\{(\w+)\}\}", sub, template)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for old in ASSETS.glob("*.webp"):
        old.unlink()
    dev, dev_llm = load("dev-rules-final.json"), load("dev-llm-final.json")
    h1f, h2f = load("holdout1-frozen-summary.json"), load("holdout2-frozen-v2-summary.json")
    h1, h2 = load("holdout-rules-final.json"), load("holdout2-rules-final.json")
    h1_llm, h2_llm = load("holdout-llm-final.json"), load("holdout2-llm-final.json")
    live = json.loads((RES / "live-sheets-2026-09-25.json").read_text(encoding="utf-8"))
    transcript = (RES / "chaos-transcript.txt").read_text(encoding="utf-8").strip().splitlines()

    finals = [dev, h1, h2, dev_llm, h1_llm, h2_llm]
    evaluated = dev["files"] + h1["files"] + h2["files"]
    passed_total = sum(passed_as_ok(s) for s in finals + [h1f, h2f])
    silent_total = sum(len(s["ok_with_wrong_fields"]) for s in finals)
    f = dev["fields"]
    text_n, scan_n = f["total"]["text_n"], f["total"]["scan_n"]
    text_all = all(c["text_ok"] == c["text_n"] for c in f.values())
    cost = live["llm_cost"]
    kills = sum("killed" in ln for ln in transcript)
    crashes = sum("exit 137" in ln for ln in transcript)

    by_name, table = live_rows()
    hero = [hero_scenario(split, name, title, cap, by_name) for split, name, title, cap in HERO]
    shots = screenshots()
    c_dev, p_dev = defects(dev)
    ok_n = sum(1 for r in table if r["s"] == "ok")
    values = {
        "evaluated": evaluated, "passed_total": passed_total, "silent_total": silent_total,
        "text_n": text_n, "text_pct": 100 if text_all else "<100", "scan_n": scan_n,
        "scan_currency": f"{f['currency']['scan_ok']}/{scan_n}",
        "scan_currency_llm": f"{dev_llm['fields']['currency']['scan_ok']}/{scan_n}",
        "dev_defects": f"{c_dev}/{p_dev}",
        "dev_clean": "{}/{}".format(*clean(dev)), "dev_clean_llm": "{}/{}".format(*clean(dev_llm)),
        "h1_clean": "{}/{}".format(*clean(h1)), "h1_clean_llm": "{}/{}".format(*clean(h1_llm)),
        "h2_clean": "{}/{}".format(*clean(h2)), "h2_clean_llm": "{}/{}".format(*clean(h2_llm)),
        "tests": test_count(), "kills": kills, "crashes": crashes,
        "per_page": f"{cost['usd'] / cost['calls']:.4f}", "llm_calls": cost["calls"], "llm_usd": f"{cost['usd']:.3f}",
        "llm_pages": sum(s["llm"]["calls"] for s in (dev_llm, h1_llm, h2_llm)), "llm_model": cost["model"],
        "live_first": live["first_run"]["invoices_appended"], "live_lines": live["first_run"]["line_items_appended"],
        "live_injected": sum(live["lost_response_resync"]["injected"].values()),
        "live_lost": live["lost_response_resync"]["injected"]["lost_response"],
        "live_pruned": live["reprocess_with_prune"]["invoices_pruned"],
        "table_n": len(table), "table_ok": ok_n, "table_review": len(table) - ok_n,
        "timeline": timeline(git_log(), h1f, h2f, h1, h2),
        "contact": contact_sheet(),
        "shots": "".join(f'<figure class="shot"><img src="{s["src"]}" width="{s["w"]}" height="{s["h"]}" loading="lazy" '
                         f'alt="Live Google Sheet, {esc(s["label"])}"><figcaption>{esc(s["label"])}</figcaption></figure>'
                         for s in shots),
        "hero_json": json.dumps(hero, ensure_ascii=False).replace("</", "<\\/"),
        "table_json": json.dumps(table, ensure_ascii=False).replace("</", "<\\/"),
        "transcript_json": json.dumps(transcript).replace("</", "<\\/"),
        "diff": diff_table(by_name),
        "hero_first_img": hero[0]["img"], "hero_first_w": hero[0]["w"], "hero_first_h": hero[0]["h"],
    }
    (SITE / "index.html").write_text(render(values), encoding="utf-8")
    print("site/index.html written;", {k: v for k, v in values.items() if not k.endswith("_json")
                                       and k not in ("timeline", "contact", "shots")})


if __name__ == "__main__":
    main()
