"""Command line entry point.

    invoice-pipeline run --inbox data/dev/inbox --state state/dev.sqlite --sink file:out/sheets
    invoice-pipeline run --inbox ... --sink sheets:<spreadsheet-id> --credentials sa.json
    invoice-pipeline report --state state/dev.sqlite --out reports/
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from invoice_pipeline.pipeline import Pipeline
from invoice_pipeline.state import State


def make_sink(spec: str | None, credentials: str | None):
    if not spec or spec == "none":
        return None
    kind, _, target = spec.partition(":")
    if kind == "file":
        from invoice_pipeline.sinks import FileSink

        return FileSink(target)
    if kind == "sheets":
        from invoice_pipeline.sinks import SheetsSink

        if not credentials:
            sys.exit("--credentials (service account JSON) is required for the sheets sink")
        return SheetsSink(target, credentials)
    if kind == "flaky":  # file sink with injected failures, for demonstrating recovery
        from invoice_pipeline.sinks import FileSink, FlakySink

        return FlakySink(FileSink(target), seed=1)
    sys.exit(f"unknown sink {spec!r}")


def make_fallback(enabled: bool):
    if not enabled:
        return None
    from invoice_pipeline.llm import LLMFallback

    return LLMFallback()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="invoice-pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="process new files in the inbox and sync rows")
    run.add_argument("--inbox", type=Path, required=True)
    run.add_argument("--state", type=Path, default=Path("state/pipeline.sqlite"))
    run.add_argument("--sink", default="none", help="none | file:<dir> | flaky:<dir> | sheets:<spreadsheet-id>")
    run.add_argument("--credentials", help="Google service account JSON (sheets sink)")
    run.add_argument("--as-of", type=date.fromisoformat, default=None, help="treat this date as today")
    run.add_argument("--llm", action="store_true", help="LLM fallback for scans that fail validation")
    run.add_argument("--ocr-cache", default=None)
    run.add_argument("--prune", action="store_true",
                     help="delete sheet rows this state no longer produces (sheet must be written only by this pipeline)")

    sync = sub.add_parser("sync", help="push pending rows only (e.g. after a failed sync)")
    sync.add_argument("--state", type=Path, default=Path("state/pipeline.sqlite"))
    sync.add_argument("--sink", required=True)
    sync.add_argument("--credentials")

    rep = sub.add_parser("report", help="write the run report and the review queue")
    rep.add_argument("--state", type=Path, default=Path("state/pipeline.sqlite"))
    rep.add_argument("--out", type=Path, default=Path("reports"))

    args = ap.parse_args(argv)
    state = State(args.state)
    try:
        if args.cmd == "run":
            p = Pipeline(state, make_sink(args.sink, args.credentials), as_of=args.as_of,
                         fallback=make_fallback(args.llm), ocr_cache=args.ocr_cache)
            stats = p.run(args.inbox, prune=args.prune)
            print(f"processed {stats.processed}, skipped {stats.skipped} (already seen), "
                  f"status {stats.by_status}, synced {stats.synced}, retries {stats.retries}, "
                  f"llm fallback {stats.fallback_used}")
        elif args.cmd == "sync":
            p = Pipeline(state, make_sink(args.sink, args.credentials))
            p.sync()
            print(f"synced {p.stats.synced}, retries {p.stats.retries}")
        elif args.cmd == "report":
            from invoice_pipeline.report import write_report

            paths = write_report(state, args.out)
            print("\n".join(str(p) for p in paths))
    finally:
        state.close()


if __name__ == "__main__":
    main()
