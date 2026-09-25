"""Orchestration: inbox -> extract -> validate -> state/outbox -> sink.

Safe to re-run at any point. A file already processed (same bytes) is skipped; a killed run
resumes from the outbox; rows are upserted by key, so the sheet never gets a duplicate row.
"""

from __future__ import annotations

import hashlib
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from invoice_pipeline import taxid
from invoice_pipeline.layout import read_page
from invoice_pipeline.models import ExtractionResult, Invoice, Issue, Severity, Status
from invoice_pipeline.reconcile import reconcile
from invoice_pipeline.rules import extract
from invoice_pipeline.sinks import RetryableError, Sink
from invoice_pipeline.state import State
from invoice_pipeline.validate import validate

INVOICE_SHEET = "invoices"
LINE_SHEET = "line_items"
INVOICE_HEADER = [
    "key", "status", "vendor_name", "vendor_tax_id", "invoice_number", "issue_date", "due_date", "currency",
    "subtotal", "tax_rate", "tax_amount", "total", "line_count", "issues", "notes", "source_file", "method",
]
LINE_HEADER = ["key", "invoice_key", "line", "description", "quantity", "unit_price", "amount", "currency"]
HEADERS = {INVOICE_SHEET: INVOICE_HEADER, LINE_SHEET: LINE_HEADER}

Fallback = Callable[[str, ExtractionResult], ExtractionResult | None]


def with_retry(fn: Callable, *, attempts: int = 7, base: float = 0.5, cap: float = 30.0,
               sleep: Callable[[float], None] = time.sleep, rng: random.Random | None = None,
               on_retry: Callable[[int, Exception], None] | None = None):
    """Exponential backoff with full jitter; honours Retry-After when the server sends one."""
    rng = rng or random.Random()
    for attempt in range(attempts):
        try:
            return fn()
        except RetryableError as e:
            if attempt == attempts - 1:
                raise
            delay = rng.uniform(0, min(cap, base * 2**attempt))
            if e.retry_after:
                delay = max(delay, e.retry_after)
            if on_retry:
                on_retry(attempt + 1, e)
            sleep(delay)


SUSPICIOUS = {"LOW_CONFIDENCE", "LINE_AMOUNT_MISMATCH", "LINES_SUM_MISMATCH", "TAX_MISMATCH", "TOTAL_MISMATCH",
              "NO_LINE_ITEMS", "INVALID_TAX_ID"}


def needs_fallback(result: ExtractionResult, issues: list[Issue]) -> bool:
    """Scans whose validation failed in a way a misread could explain. A text layer is read
    exactly, so its arithmetic errors are the vendor's and never go to the model."""
    if result.method == "text":
        return False
    return any(i.code.startswith("MISSING_") or i.code in SUSPICIOUS for i in issues)


def _num(v):
    return None if v is None else float(v)


def invoice_key(inv: Invoice, sha: str) -> str:
    if inv.vendor_tax_id and inv.invoice_number:
        return f"{taxid.normalize(inv.vendor_tax_id)}|{inv.invoice_number.strip().upper()}"
    return f"file:{sha[:12]}"  # cannot be matched to anything: stays unique, goes to review


def _same_content(a: dict, b: Invoice) -> bool:
    return (str(a.get("total")) == str(b.total) and str(a.get("issue_date")) == str(b.issue_date)
            and str(a.get("subtotal")) == str(b.subtotal))


@dataclass
class RunStats:
    processed: int = 0
    skipped: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    synced: dict[str, dict[str, int]] = field(default_factory=dict)
    retries: int = 0
    fallback_used: int = 0


class Pipeline:
    def __init__(self, state: State, sink: Sink | None, *, as_of: date | None = None,
                 fallback: Fallback | None = None, ocr_cache: str | None = None, batch_size: int = 100,
                 sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print):
        self.state = state
        self.sink = sink
        self.as_of = as_of or date.today()
        self.fallback = fallback
        self.ocr_cache = ocr_cache
        self.batch_size = batch_size
        self.sleep = sleep
        self.log = log
        self.stats = RunStats()

    # --- processing -----------------------------------------------------------------------------------
    def process_file(self, path: Path) -> str | None:
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if self.state.seen(sha):
            self.stats.skipped += 1
            return None

        result = extract(read_page(str(path), self.ocr_cache))
        issues = validate(result.invoice, self.as_of, result.confidence)
        if self.fallback and needs_fallback(result, issues):
            second = self.fallback(str(path), result)
            if second is not None:
                self.stats.fallback_used += 1
                result = reconcile(result, second)
                issues = validate(result.invoice, self.as_of, result.confidence, conflicts=result.conflicts)

        inv, notes = result.invoice, list(result.notes)
        if inv.vendor_tax_id and taxid.check(inv.vendor_tax_id)[0]:
            tid = taxid.normalize(inv.vendor_tax_id)
            known = self.state.vendor(tid)
            if result.method == "text" and inv.vendor_name:
                self.state.learn_vendor(tid, inv.vendor_name, path.name)
            elif known and known != inv.vendor_name:
                notes.append(f"vendor name '{inv.vendor_name}' replaced by registry name for {tid}")
                inv.vendor_name = known

        key = invoice_key(inv, sha)
        status = Status.NEEDS_REVIEW if issues else Status.OK
        previous = self.state.by_key(key) if not key.startswith("file:") else None
        if previous:
            if _same_content(previous.invoice, inv):
                status = Status.DUPLICATE
                notes.append(f"duplicate of {previous.name}")
            else:
                issues.append(Issue(code="DUPLICATE_INVOICE", severity=Severity.ERROR,
                                    message=f"same vendor and number as {previous.name}, different content"))
                status = Status.NEEDS_REVIEW
                key = f"{key}#conflict-{sha[:8]}"

        rows: dict[str, list] = {}
        if status != Status.DUPLICATE:
            rows[INVOICE_SHEET] = [(key, [
                key, status.value, inv.vendor_name, inv.vendor_tax_id, inv.invoice_number,
                inv.issue_date and inv.issue_date.isoformat(), inv.due_date and inv.due_date.isoformat(),
                inv.currency, _num(inv.subtotal), _num(inv.tax_rate), _num(inv.tax_amount), _num(inv.total),
                len(inv.line_items), "; ".join(f"{i.code}: {i.message}" for i in issues), "; ".join(notes),
                path.name, result.method,
            ])]
            rows[LINE_SHEET] = [(f"{key}#{n}", [
                f"{key}#{n}", key, n, li.description, _num(li.quantity), _num(li.unit_price), _num(li.amount),
                inv.currency,
            ]) for n, li in enumerate(inv.line_items, 1)]

        self.state.record(
            sha=sha, name=path.name, status=status.value, key=key, method=result.method,
            confidence=result.confidence, issues=[i.model_dump() for i in issues], notes=notes,
            invoice=inv.model_dump(mode="json"), processed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            rows=rows)
        self.stats.processed += 1
        self.stats.by_status[status.value] = self.stats.by_status.get(status.value, 0) + 1
        return status.value

    def process_inbox(self, inbox: Path) -> None:
        for path in sorted(inbox.glob("*.pdf")):
            status = self.process_file(path)
            if status:
                self.log(f"{path.name}: {status}")

    # --- sync ------------------------------------------------------------------------------------------
    def sync(self, prune: bool = False) -> None:
        if self.sink is None:
            return
        for sheet in (INVOICE_SHEET, LINE_SHEET):
            pending = self.state.pending(sheet)
            totals = {"updated": 0, "appended": 0}
            for i in range(0, len(pending), self.batch_size):
                batch = pending[i : i + self.batch_size]

                def attempt(batch=batch):
                    return self.sink.upsert(sheet, HEADERS[sheet], [row for _, row, _ in batch])

                def count(n, e):
                    self.stats.retries += 1
                    self.log(f"  retry {n} ({sheet}): {e}")

                result = with_retry(attempt, sleep=self.sleep, on_retry=count)
                self.state.mark_synced(sheet, [(k, h) for k, _, h in batch])
                for k in totals:
                    totals[k] += result[k]
            if prune:  # only for a sheet this pipeline writes alone (see README, Limits)
                totals["pruned"] = with_retry(lambda: self.sink.prune(sheet, self.state.keys(sheet)),
                                              sleep=self.sleep)
            self.stats.synced[sheet] = totals

    def run(self, inbox: Path, prune: bool = False) -> RunStats:
        self.process_inbox(inbox)
        self.sync(prune)
        return self.stats
