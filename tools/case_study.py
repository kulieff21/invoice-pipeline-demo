"""Build the case-study page (site/index.html) from measurement files. No number is typed by hand.

    uv run python tools/case_study.py

Inputs: results/*.json, tests/ (test count), data/*/labels.jsonl + PDFs (layout thumbnails),
tools/screenshots/*.png (live Google Sheet, taken by hand).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
ASSETS = SITE / "assets"
RES = ROOT / "results"


def load(name: str) -> dict:
    data = json.loads((RES / name).read_text(encoding="utf-8"))
    return data.get("summary", data)


def frac(n: int, d: int) -> str:
    return f"{n}&nbsp;/&nbsp;{d}"


def passed_as_ok(s: dict) -> int:
    if "defective_passed_as_ok" in s:
        return len(s["defective_passed_as_ok"])
    # older summaries: a caught defect has at least one issue, so it cannot have passed
    caught = sum(v["caught"] for v in s["defects"].values())
    planted = sum(v["planted"] for v in s["defects"].values())
    if caught != planted:
        raise SystemExit("cannot derive defective_passed_as_ok")
    return 0


def defects(s: dict) -> tuple[int, int]:
    return (sum(v["caught"] for v in s["defects"].values()), sum(v["planted"] for v in s["defects"].values()))


def test_count() -> int:
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=ROOT, capture_output=True,
                         text=True).stdout
    m = re.search(r"(\d+) tests? collected", out)
    if not m:
        raise SystemExit("pytest collection failed:\n" + out[-2000:])
    return int(m.group(1))


def chaos_counts() -> tuple[int, int]:
    src = (ROOT / "tests" / "test_chaos.py").read_text(encoding="utf-8")
    kills = re.search(r"for delay in \(([^)]*)\)", src).group(1)
    crashes = re.search(r"for crash_after in \(([^)]*)\)", src).group(1)
    return len(kills.split(",")), len(crashes.split(","))


# --- images ---------------------------------------------------------------------------------------------
SAMPLES = [
    ("dev", "uk", False, "UK · English", "development"),
    ("dev", "de", False, "Germany · German", "development"),
    ("dev", "us", False, "US · English", "development"),
    ("dev", "fr", False, "France · French", "development"),
    ("dev", "de", True, "Scan · 150 dpi, skew, noise", "development"),
    ("holdout", "nl", False, "Netherlands · stacked labels", "holdout 1"),
    ("holdout", "it", False, "Italy · no colon, no symbol", "holdout 1"),
    ("holdout2", "es", False, "Spain · discount column", "holdout 2"),
    ("holdout2", "ch", False, "Switzerland · CHF, UID", "holdout 2"),
]


def thumbnails() -> list[dict]:
    out = []
    for split, layout, scanned, caption, group in SAMPLES:
        labels = [json.loads(line) for line in open(ROOT / "data" / split / "labels.jsonl", encoding="utf-8")]
        rec = next(r for r in labels if r["layout"] == layout and r["scanned"] == scanned and r["defect"] is None
                   and len(r["invoice"]["line_items"]) >= 3)
        page = pdfium.PdfDocument(str(ROOT / "data" / split / "inbox" / rec["file"]))[0]
        img = page.render(scale=1.6).to_pil().convert("RGB")
        img = img.crop((0, 0, img.width, int(img.height * 0.56)))
        name = f"invoice-{split}-{layout}{'-scan' if scanned else ''}.webp"
        img.save(ASSETS / name, "WEBP", quality=82, method=6)
        out.append({"src": f"assets/{name}", "caption": caption, "group": group, "w": img.width, "h": img.height})
    return out


def screenshots() -> list[dict]:
    """Rows 1-21 only: row 22 shows a message wording that was corrected after the screenshot."""
    shots = []
    for name, label in (("sheet-keys", "Keys & status"), ("sheet-issues", "Amounts & issues"),
                        ("sheet-notes", "Notes")):
        img = Image.open(ROOT / "tools" / "screenshots" / f"{name}.png").convert("RGB")
        img = img.crop((0, 0, img.width, min(img.height, 584)))
        img.save(ASSETS / f"{name}.webp", "WEBP", quality=88, method=6)
        shots.append({"src": f"assets/{name}.webp", "label": label, "w": img.width, "h": img.height})
    return shots


# --- page ------------------------------------------------------------------------------------------------
def render(values: dict) -> str:
    template = (ROOT / "tools" / "case_template.html").read_text(encoding="utf-8")

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise KeyError(f"template placeholder {{{{{key}}}}} has no value")
        return str(values[key])

    html = re.sub(r"\{\{(\w+)\}\}", sub, template)
    return html


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    dev, dev_llm = load("dev-rules-final.json"), load("dev-llm-final.json")
    h1_frozen, h2_frozen = load("holdout1-frozen-summary.json"), load("holdout2-frozen-v2-summary.json")
    h1, h2 = load("holdout-rules-final.json"), load("holdout2-rules-final.json")
    h1_llm, h2_llm = load("holdout-llm-final.json"), load("holdout2-llm-final.json")
    live = json.loads((RES / "live-sheets-2026-09-25.json").read_text(encoding="utf-8"))

    all_sets = [dev, h1, h2, dev_llm, h1_llm, h2_llm, h1_frozen, h2_frozen]
    evaluated = dev["files"] + h1["files"] + h2["files"]
    passed_total = sum(passed_as_ok(s) for s in all_sets)
    silent_total = sum(len(s["ok_with_wrong_fields"]) for s in (dev, h1, h2, dev_llm, h1_llm, h2_llm))

    f = dev["fields"]
    text_n = f["total"]["text_n"]
    text_all = all(c["text_ok"] == c["text_n"] for c in f.values())
    scan_core = ["vendor_name", "vendor_tax_id", "invoice_number", "issue_date", "due_date", "subtotal", "tax_rate",
                 "tax_amount", "total", "line_items"]
    scan_n = f["total"]["scan_n"]
    scan_core_ok = min(f[k]["scan_ok"] for k in scan_core)

    kills, crashes = chaos_counts()
    cost = live["llm_cost"]
    per_page = cost["usd"] / cost["calls"]
    llm_pages = sum(s["llm"]["calls"] for s in (dev_llm, h1_llm, h2_llm))

    def cf(s):  # clean invoices flagged
        return s["clean"]["flagged"], s["clean"]["total"]

    rows = [
        ("Holdout 1 · NL, IT", cf(h1_frozen), cf(h1), cf(h1_llm), passed_as_ok(h1_frozen)),
        ("Holdout 2 · ES, CH", cf(h2_frozen), cf(h2), cf(h2_llm), passed_as_ok(h2_frozen)),
    ]
    bars = []
    for name, frozen, fixed, llm, p in rows:
        bars.append(f"""
      <div class="bargroup reveal">
        <div class="bargroup-head"><strong>{name}</strong><span>defective passed as ok: <b class="zero">{p}</b></span></div>
        <div class="bar"><span class="bar-label">Frozen rules, first sight</span><span class="track"><i class="fill fill-bad" data-w="{100 * frozen[0] / frozen[1]:.1f}" style="width:{100 * frozen[0] / frozen[1]:.1f}%"></i></span><span class="bar-val">{frac(*frozen)}</span></div>
        <div class="bar"><span class="bar-label">After general fixes (seen)</span><span class="track"><i class="fill fill-mid" data-w="{100 * fixed[0] / fixed[1]:.1f}" style="width:{100 * fixed[0] / fixed[1]:.1f}%"></i></span><span class="bar-val">{frac(*fixed)}</span></div>
        <div class="bar"><span class="bar-label">+ LLM second reader</span><span class="track"><i class="fill fill-good" data-w="{100 * llm[0] / llm[1]:.1f}" style="width:{100 * llm[0] / llm[1]:.1f}%"></i></span><span class="bar-val">{frac(*llm)}</span></div>
      </div>""")

    gallery = "".join(
        f"""
        <figure class="thumb reveal" tabindex="0"><div class="thumb-img"><img src="{t['src']}" width="{t['w']}" height="{t['h']}" loading="lazy" alt="Synthetic invoice, {t['caption']}"></div>
          <figcaption><span class="tag tag-{t['group'].replace(' ', '')}">{t['group']}</span>{t['caption']}</figcaption></figure>"""
        for t in thumbnails())

    shots = screenshots()
    tabs = "".join(
        f'<button class="tab" role="tab" aria-selected="{"true" if i == 0 else "false"}" data-tab="{i}">{s["label"]}</button>'
        for i, s in enumerate(shots))
    panes = "".join(
        f'<img class="pane{" on" if i == 0 else ""}" data-pane="{i}" src="{s["src"]}" width="{s["w"]}" height="{s["h"]}" loading="lazy" alt="Live Google Sheet: {s["label"]}">'
        for i, s in enumerate(shots))

    c_dev, p_dev = defects(dev)
    c_h2f, p_h2f = defects(h2_frozen)
    values = {
        "evaluated": evaluated,
        "passed_total": passed_total,
        "silent_total": silent_total,
        "text_n": text_n,
        "text_pct": 100 if text_all else "<100",
        "scan_n": scan_n,
        "scan_core_ok": scan_core_ok,
        "scan_currency": frac(f["currency"]["scan_ok"], scan_n),
        "scan_currency_llm": frac(dev_llm["fields"]["currency"]["scan_ok"], scan_n),
        "dev_defects": frac(c_dev, p_dev),
        "dev_clean": frac(*cf(dev)),
        "dev_clean_llm": frac(*cf(dev_llm)),
        "h1f_clean": frac(*cf(h1_frozen)),
        "h2f_clean": frac(*cf(h2_frozen)),
        "h2f_defects": frac(c_h2f, p_h2f),
        "tests": test_count(),
        "kills": kills,
        "crashes": crashes,
        "per_page": f"{per_page:.4f}",
        "per_page_num": f"{per_page:.4f}",
        "llm_calls": cost["calls"],
        "llm_usd": f"{cost['usd']:.3f}",
        "llm_pages": llm_pages,
        "llm_model": cost["model"],
        "live_first": live["first_run"]["invoices_appended"],
        "live_lines": live["first_run"]["line_items_appended"],
        "live_injected": sum(live["lost_response_resync"]["injected"].values()),
        "live_lost": live["lost_response_resync"]["injected"]["lost_response"],
        "live_pruned": live["reprocess_with_prune"]["invoices_pruned"],
        "bars": "".join(bars),
        "gallery": gallery,
        "tabs": tabs,
        "panes": panes,
    }
    (SITE / "index.html").write_text(render(values), encoding="utf-8")
    print("site/index.html written;", {k: v for k, v in values.items() if k not in ("bars", "gallery", "tabs", "panes")})


if __name__ == "__main__":
    main()
