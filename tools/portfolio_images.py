"""Compose the Upwork portfolio images (1000x750, rendered at 2x) in the case study's visual style.

    uv run python tools/portfolio_images.py      # writes build/portfolio/*.html
    node <shots script> build/portfolio          # renders them to PNG (see AGENTS.md)

Numbers and examples come from the same sources as the case study: results/, the hero data
embedded in site/index.html, results/chaos-transcript.txt and the git history.
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import case_study as cs  # noqa: E402

OUT = ROOT / "build" / "portfolio"
PAGE = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
CSS = re.search(r"<style>(.*?)</style>", PAGE, re.S).group(1)
HERO = json.loads(re.search(r'<script id="hero-data" type="application/json">(.*?)</script>', PAGE, re.S).group(1)
                  .replace("<\\/", "</"))
FONTS = re.search(r'(<link href="https://fonts.googleapis.com/css2[^>]+>)', PAGE).group(1)

FRAME = """
html,body{width:1000px;height:750px;overflow:hidden;background:var(--paper)}
body{background-image:radial-gradient(rgba(28,27,25,.035) 1px,transparent 1px);background-size:3px 3px}
.frame{position:absolute;inset:0;padding:40px 46px}
.brand{position:absolute;left:46px;right:46px;bottom:22px;display:flex;justify-content:space-between;
  font:500 .68rem/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--muted);border-top:1px solid var(--rule);padding-top:10px}
.brand b{color:var(--ink);font-weight:600}
.kick{font:500 .72rem/1.3 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--red)}
.h{font:600 2.35rem/1.04 var(--serif);letter-spacing:-.02em;margin:10px 0 0;font-variation-settings:"opsz" 144}
.h em{font-style:italic;font-weight:400;color:var(--blue)}
.inv{position:relative;background:#fff;box-shadow:0 1px 0 var(--rule),0 18px 40px -18px rgba(28,27,25,.35),0 2px 6px rgba(28,27,25,.08);overflow:hidden}
.inv img{display:block;width:100%;height:auto}
.inv .mk{opacity:1;transform:none}
.inv .stamp{opacity:.9;transform:rotate(-6deg);animation:none}
.cap{font:italic .95rem/1.3 var(--serif);color:var(--ink2);margin-top:8px}
.cap b{font:600 .66rem var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--red);font-style:normal;margin-right:6px}
"""


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def invoice(s: dict, stamp_size: str = "") -> str:
    marks = []
    for m in s["marks"]:
        for kind, key in (("key", "key"), ("tbl" if m["field"] == "line_items" else "val", "value")):
            if key in m:
                x, y, w, h = m[key]
                marks.append(f'<div class="mk {kind}" style="left:{x - .5}%;top:{y - .35}%;width:{w + 1}%;height:{h + .7}%"></div>')
    ok = s["status"] == "ok"
    stamp = f'<div class="stamp {"ok" if ok else "review"}" style="{stamp_size}">{"OK · synced" if ok else "Needs review"}</div>'
    return (f'<div class="inv"><img src="../../site/{s["img"]}" width="{s["w"]}" height="{s["h"]}">'
            f'<div class="view" style="position:absolute;inset:0">{"".join(marks)}</div>{stamp}</div>')


def row(s: dict) -> str:
    r = s["row"]
    cells = "".join(f"<div{' class=\"llm\"' if k in s['llm'] else ' class=\"fill\"'}>{esc(r[k])}</div>"
                    for k in ("vendor", "number", "total"))
    st = "ok" if s["status"] == "ok" else "review"
    issue = s["issues"][0] if s["issues"] else "No issues. Every check passed."
    code, _, msg = issue.partition(": ") if s["issues"] else ("", "", issue)
    foot = (f'<span class="code">{esc(code)}</span>{esc(msg)}' if code else esc(msg))
    return (f'<div class="row" style="margin-top:12px"><div class="hd" style="grid-template-columns:1.35fr 1.05fr .85fr 1.15fr">'
            f'<div>vendor</div><div>number</div><div>total</div><div>status</div></div>'
            f'<div class="rw" style="grid-template-columns:1.35fr 1.05fr .85fr 1.15fr">{cells}<div class="st {st}">{st.replace("review", "needs_review")}</div></div>'
            f'<div class="foot">{foot}</div></div>')


def page(body: str, extra_css: str = "") -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">{FONTS}<style>{CSS}{FRAME}{extra_css}</style></head>'
            f'<body><div class="frame">{body}</div><div class="brand"><span><b>Invoice pipeline</b> · Python · demo with synthetic data</span>'
            f'<span>Elmar Guliyev</span></div></body></html>')


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    dev, dev_llm = cs.load("dev-rules-final.json"), cs.load("dev-llm-final.json")
    h1f, h2f = cs.load("holdout1-frozen-summary.json"), cs.load("holdout2-frozen-v2-summary.json")
    h1, h2 = cs.load("holdout-rules-final.json"), cs.load("holdout2-rules-final.json")
    h1_llm, h2_llm = cs.load("holdout-llm-final.json"), cs.load("holdout2-llm-final.json")
    finals = [dev, h1, h2, dev_llm, h1_llm, h2_llm]
    evaluated = dev["files"] + h1["files"] + h2["files"]
    passed = sum(cs.passed_as_ok(s) for s in finals + [h1f, h2f])
    tests = cs.test_count()
    transcript = (ROOT / "results" / "chaos-transcript.txt").read_text(encoding="utf-8").strip().splitlines()
    live = json.loads((ROOT / "results" / "live-sheets-2026-09-25.json").read_text(encoding="utf-8"))
    by = {s["file"]: s for s in HERO}

    # 1. cover
    s = by["inv_0011.pdf"]
    ledger = "".join(
        f'<div class="ledger-row"><span class="k">{k}</span><span class="lead"></span><span class="v{z}">{v}</span></div>'
        for k, v, z in [("Test invoices, incl. unseen layouts", evaluated, ""),
                        ("Defective invoices that reached the sheet as “ok”", passed, " zero"),
                        ("Automated tests, incl. kill -9", tests, "")])
    (OUT / "01-cover.html").write_text(page(f"""
      <div style="display:grid;grid-template-columns:1fr 1.08fr;gap:40px;height:640px">
        <div style="display:flex;flex-direction:column">
          <div class="kick">Python · Google Sheets · OCR + LLM</div>
          <h1 class="h" style="font-size:2.75rem">Reads the invoice. Checks the maths. <em>Says when it isn’t sure.</em></h1>
          <p style="color:var(--ink2);font-size:1.02rem;margin:18px 0 0;max-width:36ch">Every invoice is checked: line maths, tax, totals, dates, tax-ID check digits, duplicates. What fails is held for a person, with the reason.</p>
          <div class="ledger" style="margin-top:auto">{ledger}</div>
        </div>
        <div style="padding-top:8px">
          {invoice(s)}
          {row(s)}
          <p class="cap" style="margin-top:10px">A real test invoice: the pipeline marks what it read, writes the row and holds the invoice, because the vendor's line does not multiply out.</p>
        </div>
      </div>"""), encoding="utf-8")

    # 2. four readings
    cards = []
    for f in ("inv_0002.pdf", "inv_0011.pdf", "inv_0053.pdf", "inv_0108.pdf"):
        s = by[f]
        cards.append(f'<div><div style="height:228px;overflow:hidden">{invoice(s, "font-size:1rem;padding:6px 10px;border-width:3px")}</div>'
                     f'<div class="cap"><b>{esc(s["source"])}</b>{esc(s["title"])}. {esc(s["caption"])}</div></div>')
    (OUT / "02-four-cases.html").write_text(page(f"""
      <div class="kick">The same checks on every page</div>
      <h1 class="h" style="font-size:1.9rem;margin-bottom:18px">Read, checked, stamped: <em>four real test invoices</em></h1>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px 26px">{''.join(cards)}</div>"""), encoding="utf-8")

    # 3. unseen layouts
    commits = cs.git_log()
    find = lambda prefix: next(c for c in commits if c["s"].startswith(prefix))  # noqa: E731
    steps = [
        (find("Add LLM fallback"), "freeze", "Rules frozen", None),
        (find("Add holdout layouts (nl, it)"), "write", "Dutch and Italian layouts written", None),
        (find("Record holdout-1"), "measure", "First sight", (cs.clean(h1f), cs.passed_as_ok(h1f), "bad")),
        (find("Rules v2"), "freeze", "General fixes, frozen again", (cs.clean(h1), None, "mid")),
        (find("Add holdout-2 layouts"), "write", "Spanish and Swiss layouts written", None),
        (find("Record holdout-2"), "measure", "First sight", (cs.clean(h2f), cs.passed_as_ok(h2f), "bad")),
        (find("Rules v3"), "fix", "Fixes for what broke", (cs.clean(h2), None, "mid")),
    ]
    items = []
    for c, kind, note, res in steps:
        tag = f'<span class="ref">{esc(c["tag"])}</span>' if c["tag"] else ""
        extra = ""
        if res:
            (n, d), p, tone = res
            extra = (f'<div class="gl-res" style="margin-top:4px"><span class="gl-bar"><i class="fill {tone}" style="width:{100 * n / d:.1f}%"></i></span>'
                     f'<span class="mono">{n}/{d} clean to review</span>'
                     + (f'<span class="mono ok">· {p} passed as ok</span>' if p is not None else "") + "</div>")
        items.append(f'<li class="gl gl-{kind}" style="padding:6px 0"><span class="dot"></span><div><span class="hash">{c["h"]}</span>{tag}'
                     f'<span class="note" style="display:inline;margin:0">{esc(note)}</span>{extra}</div></li>')
    tl = "".join(items)
    (OUT / "03-unseen-layouts.html").write_text(page(f"""
      <div style="display:grid;grid-template-columns:1.55fr .85fr;gap:30px">
        <div>
          <div class="kick">Tested on layouts it had never seen</div>
          <h1 class="h" style="font-size:1.8rem">Rules frozen with a git tag, <em>then</em> new layouts written</h1>
          <ol class="gitlog" style="margin-top:14px;font-size:.92em">{tl}</ol>
        </div>
        <div style="padding-top:120px;text-align:center">
          <div class="bigstamp" style="opacity:.92;transform:rotate(-5deg);font-size:6.5rem">{passed}</div>
          <p style="font:600 1.12rem/1.3 var(--serif);margin:26px 0 8px">defective invoices reached the sheet as “ok”</p>
          <p style="font-size:.9rem;color:var(--ink2);margin:0">When reading failed on a new layout, the invoice went to review instead of into the sheet with wrong data.</p>
        </div>
      </div>
      <p class="margin-note" style="position:absolute;left:46px;right:46px;bottom:70px;margin:0">The holdout layouts were written to hit known weak spots. Only first sight is a test: after a round's fixes, its numbers are on seen data and are marked that way.</p>""", ".gl .note{font-size:.84rem}.gl .msg{font-size:.76rem}.gl-bar{width:150px}"), encoding="utf-8")

    # 4. crashes + live sheet
    colour = (lambda l: html.escape(l).replace("killed", '<span class="k">killed</span>').replace("exit 137", '<span class="x">exit 137</span>')
              .replace("exit 0", '<span class="g">exit 0</span>').replace("equal to a clean run", '<span class="g">equal to a clean run</span>'))
    term = '<span class="p">$</span> pytest -s tests/test_chaos.py\n' + "\n".join(colour(l) for l in transcript if not l.startswith("kills=")) + '\n<span class="g">1 passed</span>'
    lr = live["lost_response_resync"]
    (OUT / "04-crash-safe.html").write_text(page(f"""
      <div class="kick">Crash-safe, retried, idempotent</div>
      <h1 class="h" style="font-size:1.9rem;margin-bottom:16px">Kill it mid-run. Run it again. <em>No row written twice.</em></h1>
      <div style="display:grid;grid-template-columns:1.5fr 1fr;gap:24px">
        <div class="term"><div class="bar"><i></i><i></i><i></i></div><pre style="min-height:0;font-size:.62rem;padding:12px 14px">{term}</pre></div>
        <div>
          <div class="shot" style="padding:5px"><img src="../../site/assets/sheet-keys.webp" style="height:236px;width:100%;object-fit:cover;object-position:left top"></div>
          <div class="cap" style="font-size:.84rem">The live Google Sheet written by the pipeline.</div>
          <div class="ledger" style="margin-top:12px">
            <div class="ledger-row"><span class="k">Second run, same inbox</span><span class="lead"></span><span class="v" style="font-size:.95rem">0 written</span></div>
            <div class="ledger-row"><span class="k">{sum(lr["injected"].values())} injected faults, {lr["injected"]["lost_response"]} lost after the write</span><span class="lead"></span><span class="v" style="font-size:.95rem">0 appended</span></div>
          </div>
        </div>
      </div>""", ".ledger-row .k{font-size:.84rem}"), encoding="utf-8")
    print("written:", sorted(p.name for p in OUT.glob("*.html")))


if __name__ == "__main__":
    main()
