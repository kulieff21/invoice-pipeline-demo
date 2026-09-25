import json
import random
import shutil
from datetime import date
from pathlib import Path

import pytest

from datagen.generate import LOCALES, RENDERERS, Vendor, make_doc
from invoice_pipeline.pipeline import INVOICE_SHEET, LINE_SHEET, Pipeline
from invoice_pipeline.sinks import FileSink, FlakySink, RetryableError
from invoice_pipeline.state import State
from tests.conftest import OCR_CACHE

AS_OF = date(2026, 9, 25)


def run(inbox: Path, state_path: Path, sink, **kw):
    state = State(state_path)
    p = Pipeline(state, sink, as_of=AS_OF, sleep=lambda _: None, log=lambda _: None, ocr_cache=OCR_CACHE, **kw)
    stats = p.run(inbox)
    state.close()
    return stats


def sheet(folder: Path, name: str) -> list:
    return FileSink(folder).read(name)


def test_rerun_is_a_noop(text_inbox, tmp_path):
    first = run(text_inbox / "inbox", tmp_path / "s.sqlite", FileSink(tmp_path / "out"))
    before = sheet(tmp_path / "out", INVOICE_SHEET)
    second = run(text_inbox / "inbox", tmp_path / "s.sqlite", FileSink(tmp_path / "out"))
    assert first.processed == 40 and second.processed == 0 and second.skipped == 40
    assert sheet(tmp_path / "out", INVOICE_SHEET) == before
    keys = [r[0] for r in before[1:]]
    assert len(keys) == len(set(keys)) == 40


def test_flaky_sink_converges_to_the_same_sheet(text_inbox, tmp_path):
    run(text_inbox / "inbox", tmp_path / "clean.sqlite", FileSink(tmp_path / "clean"))
    flaky = FlakySink(FileSink(tmp_path / "flaky"), seed=4, rate_limit=0.2, server_error=0.1, lost_response=0.25)
    stats = run(text_inbox / "inbox", tmp_path / "flaky.sqlite", flaky, batch_size=5)
    assert flaky.injected["lost_response"] > 0 and stats.retries > 0
    for name in (INVOICE_SHEET, LINE_SHEET):
        assert sheet(tmp_path / "flaky", name) == sheet(tmp_path / "clean", name)


class DeadSink(FileSink):
    def upsert(self, *a, **k):
        raise RetryableError("503")


def test_outage_leaves_rows_pending_and_a_later_sync_delivers_them(text_inbox, tmp_path):
    with pytest.raises(RetryableError):
        run(text_inbox / "inbox", tmp_path / "s.sqlite", DeadSink(tmp_path / "out"))
    state = State(tmp_path / "s.sqlite")
    assert len(state.pending(INVOICE_SHEET)) == 40
    Pipeline(state, FileSink(tmp_path / "out"), sleep=lambda _: None, log=lambda _: None).sync()
    assert state.pending(INVOICE_SHEET) == []
    state.close()
    assert len(sheet(tmp_path / "out", INVOICE_SHEET)) == 41


def test_resent_copy_is_marked_duplicate(dup_inbox, tmp_path):
    labels = [json.loads(line) for line in open(dup_inbox / "labels.jsonl")]
    dups = {r["file"] for r in labels if r["defect"] == "duplicate"}
    assert dups, "fixture should contain re-sent copies"
    run(dup_inbox / "inbox", tmp_path / "s.sqlite", FileSink(tmp_path / "out"))
    state = State(tmp_path / "s.sqlite")
    status = {f["name"]: f["status"] for f in state.files()}
    state.close()
    assert {n for n, s in status.items() if s == "duplicate"} == dups
    assert len(sheet(tmp_path / "out", INVOICE_SHEET)) - 1 == len(labels) - len(dups)


def test_same_number_different_amount_goes_to_review(tmp_path):
    from faker import Faker

    rng, fake = random.Random(2), Faker("de_DE")
    vendor = Vendor("de", rng, fake)
    doc = make_doc(vendor, rng, fake)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.pdf").write_bytes(RENDERERS["de"](doc))
    desc, qty, unit, amount = doc.lines[0]
    doc.lines[0] = (desc, qty + 1, unit, amount + unit)
    doc.subtotal += unit
    from invoice_pipeline.models import money
    doc.tax = money(doc.subtotal * doc.tax_rate / 100)
    doc.total = doc.subtotal + doc.tax
    (inbox / "b.pdf").write_bytes(RENDERERS["de"](doc))
    run(inbox, tmp_path / "s.sqlite", FileSink(tmp_path / "out"))
    rows = sheet(tmp_path / "out", INVOICE_SHEET)[1:]
    assert len(rows) == 2
    b = next(r for r in rows if r[15] == "b.pdf")
    assert b[1] == "needs_review" and "DUPLICATE_INVOICE" in b[13] and "#conflict-" in b[0]


def test_prune_removes_rows_the_state_no_longer_produces(text_inbox, tmp_path):
    out = FileSink(tmp_path / "out")
    run(text_inbox / "inbox", tmp_path / "s.sqlite", out)
    table = out.read(INVOICE_SHEET)
    out._write(INVOICE_SHEET, table + [["stale|key"] + [""] * (len(table[0]) - 1)])
    state = State(tmp_path / "s.sqlite")
    p = Pipeline(state, out, sleep=lambda _: None, log=lambda _: None)
    p.sync(prune=True)
    state.close()
    assert p.stats.synced[INVOICE_SHEET]["pruned"] == 1
    assert out.read(INVOICE_SHEET) == table
