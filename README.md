# invoice-pipeline-demo

Invoice PDFs in, validated rows in Google Sheets out: safe to re-run, safe to kill, and honest
about what it could not read.

> **Demo project with synthetic data.** Every invoice here is generated (fictional companies,
> Faker addresses, generated tax IDs). The generator is in the repo, so every number below can be
> reproduced.

```
inbox/*.pdf ─► read page ─► extract fields ─► validate ─► SQLite state + outbox ─► upsert to Sheets
               text layer   labels in 6       business    one transaction          keyed rows,
               or OCR       languages         rules, tax  per file                  retried, idempotent
                            ▲                 ID checks
                            └── LLM fallback for scans that fail validation (optional)
```

## What it handles

- **Layouts it has never seen.** Labels are a multilingual vocabulary (EN, DE, FR, IT, ES, NL),
  not template coordinates. Values are found to the right of a label, below it, or in the same
  text run. Line items are read from the right, so descriptions can contain numbers and wrap.
- **Number and date formats.** `1.234,56 €`, `1 234,56 €`, `£2,258.26`, `1'234.50`, `07 Sep 2026`,
  `09/10/2026` (day/month order decided per document), ISO dates.
- **Scans.** OCR (RapidOCR, no system install), deskew from text-box angles, and repairs that use
  the invoice's own arithmetic: a lost decimal separator (`15843` → `158.43`) or a dropped
  quantity cell is recovered only if `qty × price = amount` then holds exactly, and the repair is
  written into the row's notes.
- **Business rules.** Line arithmetic (discounts included), lines vs. subtotal, tax rate vs. tax
  amount, subtotal + tax vs. total, due date before issue date, issue date in the future, missing
  fields, and vendor tax IDs with their real check digits (DE, FR, GB, CH) or format (US EIN,
  NL, IT, ES, …).
- **Duplicates.** The same file twice is skipped. A re-sent copy (same vendor and number, same
  amounts) is marked `duplicate` and gets no row. Same number with different amounts goes to
  review as `DUPLICATE_INVOICE`.
- **Failures on the way to the sheet.** Rate limits, 5xx, and the nasty one: the write succeeded
  but the response was lost. Rows are upserted by key and every retry re-reads the sheet, so a
  retried write overwrites instead of appending twice.

The pipeline never fixes a vendor's arithmetic. A total that does not add up is reported, not
recalculated: what gets paid is a business decision.

## Results

Measured with `invoice_pipeline.evaluate` against the generator's labels (`results/*.json`).
Field accuracy is exact match to the cent. 25 % of documents are scans (150 dpi, skew, noise,
JPEG).

**Development set** (layouts the rules were written against: UK, DE, US, FR; 120 invoices):

| | Text-layer PDFs (81) | Scans (39) |
|---|---|---|
| Vendor, tax ID, number, dates, amounts, tax rate, every line item | 81 / 81 | 39 / 39 |
| Currency | 81 / 81 | 26 / 39: OCR does not read `£`/`€`; for UK vendors (GBP or EUR) the invoice goes to review instead of a guess |
| Line descriptions, letter for letter | 81 / 81 | 35 / 39 (OCR letter errors) |

Planted defects caught: **31 / 31**. Re-sent copies marked duplicate: **4 / 4**. Clean invoices
sent to review: **8 / 89**, all of them UK scans with an unreadable currency symbol.

**Unseen layouts.** The rules were frozen (git tags `rules-frozen`, `rules-frozen-v2`) before
each holdout layout was written, and the tag precedes the layout commit in the history. The same
person wrote both, so this is weaker than real vendors' invoices, and the holdout layouts were
built to hit known weak spots rather than to pass.

| | Holdout 1: NL, IT (frozen v1) | Holdout 2: ES, CH (frozen v2) |
|---|---|---|
| Clean invoices sent to review | **59 / 59** | **43 / 60** |
| Planted defects caught | 21 / 21 | 15 / 20 |
| **Defective invoices that reached the sheet as "ok"** | **0** | **0** |
| What broke | label and value in one run without a colon; a VAT-% column in the table; an ID search that glued two ISO dates into a fake US EIN | currency code after a label (`Rechnungsbetrag CHF`); two label/value pairs on one line; Swiss UID format; discount column |
| After general fixes (now seen, not a test) | 1 / 59 | 9 / 60 |

The number that matters for accounts payable is the last bold row: when extraction failed on a
layout it had never seen, invoices went to review. None went through as `ok` with wrong data.

**Second reader for scans** (`results/*-llm-final.json`, model `openai/gpt-6-luna` via OpenRouter).
On its own the LLM read the currency the OCR could not, but it also dropped a repeated digit from
invoice numbers and tax IDs (`10597` → `1059`), and one such invoice went to the sheet as `ok` with
a wrong number: nothing in the business rules can see that. So the two readers are reconciled
(`reconcile.py`): agreement is taken, disagreement is decided by the tax ID's check digit or the
invoice's own arithmetic, and anything undecided goes to review as `READERS_DISAGREE`.

| Clean invoices sent to review | Rules only | Rules + LLM, reconciled |
|---|---|---|
| Development set | 8 / 89 | 3 / 89 |
| Holdout 1 (seen) | 1 / 59 | 0 / 59 |
| Holdout 2 (seen) | 9 / 60 | 4 / 60 |
| Defective invoices passed as `ok` / `ok` rows with a misread field | 0 / 0 | 0 / 0 |

35 pages went to the LLM across the three sets; 70 calls over two runs cost $0.043 on the
OpenRouter meter, about $0.0006 per page.

**Live Google Sheets** (`results/live-sheets-2026-09-25.md`): the dev inbox written to a real
spreadsheet, re-run (0 rows written), then every row re-sent through a fault injector that
drops responses after the write was applied: all rows updated in place, 0 appended, no duplicate
keys.

**Crash safety** (`tests/test_chaos.py`): the pipeline is killed with SIGKILL at arbitrary
moments and made to die right after a sheet write is applied but before it is recorded (5 + 6
times per run). The final sheets must equal a clean run, row for row, with no duplicate keys.
Replacing the upsert with a plain append makes this test and the flaky-sink test fail.

## Run it

Python 3.12, [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run python -m datagen.generate --split dev --n 120 --seed 7          # synthetic inbox + labels
uv run invoice-pipeline run --inbox data/dev/inbox --state state/dev.sqlite \
    --sink file:out/dev --as-of 2026-09-25                              # local JSON "sheets"
uv run invoice-pipeline report --state state/dev.sqlite --out reports/dev   # run report + review_queue.csv
uv run python -m invoice_pipeline.evaluate data/dev                     # accuracy vs labels
uv run pytest -q
```

Google Sheets: create a service account, share the spreadsheet with its e-mail, then

```bash
uv run invoice-pipeline run --inbox <folder> --sink sheets:<spreadsheet-id> --credentials sa.json
```

Two worksheets are created: `invoices` (one row per invoice, with `status`, `issues`, `notes`)
and `line_items`. Values are written RAW: amounts as numbers, dates as ISO text, so the sheet's
locale cannot turn `1.234` into a date.

LLM second reader (optional): `uv sync --extra llm`, add `--llm`. Used only for scans whose
validation failed; the model reads the PDF and returns JSON under a strict schema, which is then
reconciled with the OCR reading and validated again.

```bash
export ANTHROPIC_API_KEY=...                        # Anthropic (default model claude-opus-5), or:
export INVOICE_LLM_PROVIDER=openrouter OPENROUTER_API_KEY=... INVOICE_LLM_MODEL=openai/gpt-6-luna
```

## Layout

```
datagen/          synthetic invoices: layouts, tax IDs with check digits, planted defects, scans
src/invoice_pipeline/
  layout.py       page -> positioned cells (text layer or OCR, deskew)
  parse.py        amounts, numbers, dates, percentages
  rules.py        field extraction
  validate.py     business rules
  taxid.py        tax ID checks
  llm.py          optional LLM second reader (Anthropic API or OpenRouter)
  reconcile.py    OCR vs LLM: agreement, check digits, arithmetic, else review
  state.py        SQLite: files, vendor registry, outbox
  sinks.py        Google Sheets, local JSON, fault-injecting wrapper
  pipeline.py     orchestration, retry, duplicates
  report.py       run report and review queue
  evaluate.py     accuracy against labels
tests/            unit, pipeline, chaos (kill -9) tests
results/          measurement output
```

## Limits

- One page per invoice. Multi-page line tables are not handled.
- One writer per spreadsheet. Two pipelines writing the same sheet at once can collide.
- Tax IDs are checked for check digits or format, not looked up in a registry (VIES etc.).
- OCR is RapidOCR on CPU (~6 s per page). It drops `£`, `€`, some umlauts and many spaces.
- The layouts are synthetic. Real vendor invoices bring logos, stamps, handwriting and
  multi-column addresses that this data set does not have.

Built with AI assistance; every change was reviewed, tested and measured by me.

MIT License © 2026 Elmar Guliyev
