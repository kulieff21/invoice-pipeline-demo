"""Where rows end up. Every sink does keyed upserts: column A holds the row key.

`upsert` re-reads the keys from the sink on every call. The sync loop retries whole calls, so a
write that succeeded on the server but whose response was lost is seen as present on the retry
and updated in place instead of appended twice.
"""

from __future__ import annotations

import json
import os
import random
import tempfile
from pathlib import Path
from typing import Protocol


class RetryableError(Exception):
    """Transient failure: rate limit, 5xx, timeout, dropped connection."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class Sink(Protocol):
    def upsert(self, sheet: str, header: list[str], rows: list[list]) -> dict[str, int]: ...

    def read(self, sheet: str) -> list[list]: ...


def plan(existing_keys: list[str], rows: list[list]) -> tuple[dict[int, list], list[list]]:
    """Split rows into in-place updates (0-based data index -> row) and appends."""
    index = {k: i for i, k in enumerate(existing_keys)}
    updates: dict[int, list] = {}
    appended: dict[str, int] = {}
    appends: list[list] = []
    for row in rows:
        if row[0] in index:
            updates[index[row[0]]] = row
        elif row[0] in appended:  # same key twice in one batch: last one wins
            appends[appended[row[0]]] = row
        else:
            appended[row[0]] = len(appends)
            appends.append(row)
    return updates, appends


# --- local file sink (tests, demo without Google credentials) ---------------------------------------
class FileSink:
    """A folder of JSON 'sheets', written atomically (tmp file + os.replace) so a killed process
    never leaves a half-written sheet."""

    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def _path(self, sheet: str) -> Path:
        return self.folder / f"{sheet}.json"

    def read(self, sheet: str) -> list[list]:
        p = self._path(sheet)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []

    def _write(self, sheet: str, table: list[list]) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.folder, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(table, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._path(sheet))

    def upsert(self, sheet: str, header: list[str], rows: list[list]) -> dict[str, int]:
        table = self.read(sheet) or [header]
        updates, appends = plan([r[0] for r in table[1:]], rows)
        for i, row in updates.items():
            table[i + 1] = row
        table.extend(appends)
        self._write(sheet, table)
        return {"updated": len(updates), "appended": len(appends)}


class FlakySink:
    """Wraps a sink and injects the failures real APIs produce:
    - 'rate_limit' / 'server_error': the call fails before anything is written
    - 'lost_response': the write is applied, then the client sees a timeout
    """

    def __init__(self, inner: Sink, seed: int = 0, rate_limit: float = 0.2, server_error: float = 0.1,
                 lost_response: float = 0.1):
        self.inner = inner
        self.rng = random.Random(seed)
        self.p = {"rate_limit": rate_limit, "server_error": server_error, "lost_response": lost_response}
        self.injected = {k: 0 for k in self.p}

    def read(self, sheet: str) -> list[list]:
        return self.inner.read(sheet)

    def upsert(self, sheet: str, header: list[str], rows: list[list]) -> dict[str, int]:
        roll = self.rng.random()
        if roll < self.p["rate_limit"]:
            self.injected["rate_limit"] += 1
            raise RetryableError("429 Too Many Requests", retry_after=0.01)
        roll -= self.p["rate_limit"]
        if roll < self.p["server_error"]:
            self.injected["server_error"] += 1
            raise RetryableError("503 Service Unavailable")
        roll -= self.p["server_error"]
        result = self.inner.upsert(sheet, header, rows)
        if roll < self.p["lost_response"]:
            self.injected["lost_response"] += 1
            raise RetryableError("timeout after write (response lost)")
        return result


# --- Google Sheets ----------------------------------------------------------------------------------
class SheetsSink:
    """Google Sheets via a service account. Values are written RAW: amounts as numbers, dates as
    ISO text, so the spreadsheet's locale cannot reinterpret '1.234' as a date or a thousand."""

    RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

    def __init__(self, spreadsheet_id: str, credentials_file: str):
        import gspread

        self.gc = gspread.service_account(filename=credentials_file)
        self.book = self.gc.open_by_key(spreadsheet_id)

    def _ws(self, sheet: str, header: list[str]):
        import gspread

        try:
            return self.book.worksheet(sheet)
        except gspread.WorksheetNotFound:
            ws = self.book.add_worksheet(sheet, rows=1000, cols=len(header))
            ws.update([header], "A1", value_input_option="RAW")
            ws.freeze(rows=1)
            self.format_sheet(ws, header)
            return ws

    NUMBER_FORMATS = {
        "subtotal": "#,##0.00", "tax_amount": "#,##0.00", "total": "#,##0.00", "unit_price": "#,##0.00",
        "amount": "#,##0.00", "tax_rate": "0.###", "quantity": "0.##",
    }

    def format_sheet(self, ws, header: list[str]) -> None:
        """Bold header and fixed decimals: without a number format the viewer's locale shows 2904.40
        as '2904,4'. Formatting changes only how cells look; values stay as written."""
        from gspread.utils import rowcol_to_a1

        ws.format("1:1", {"textFormat": {"bold": True}})
        for i, name in enumerate(header, 1):
            if name in self.NUMBER_FORMATS:
                col = rowcol_to_a1(1, i).rstrip("1")
                ws.format(f"{col}2:{col}", {"numberFormat": {"type": "NUMBER", "pattern": self.NUMBER_FORMATS[name]}})

    def _call(self, fn, *args, **kwargs):
        import gspread
        import requests

        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            status = e.response.status_code
            if status in self.RETRYABLE_STATUS:
                ra = e.response.headers.get("Retry-After")
                raise RetryableError(f"Sheets API {status}", float(ra) if ra else None) from e
            raise
        except (requests.ConnectionError, requests.Timeout) as e:
            raise RetryableError(f"network: {e}") from e

    def read(self, sheet: str) -> list[list]:
        import gspread

        try:
            ws = self.book.worksheet(sheet)
        except gspread.WorksheetNotFound:
            return []
        return self._call(ws.get_all_values)

    def upsert(self, sheet: str, header: list[str], rows: list[list]) -> dict[str, int]:
        ws = self._ws(sheet, header)
        keys = self._call(ws.col_values, 1)[1:]  # fresh read on every attempt
        updates, appends = plan(keys, rows)
        if updates:
            data = [{"range": f"A{i + 2}", "values": [row]} for i, row in updates.items()]
            self._call(ws.batch_update, data, value_input_option="RAW")
        if appends:
            start = len(keys) + 2
            needed = start + len(appends) - 1
            if needed > ws.row_count:
                self._call(ws.add_rows, needed - ws.row_count + 200)
            # write to explicit rows instead of values.append: a retried append cannot know
            # whether the first one landed, a retried range update simply overwrites the same cells
            self._call(ws.update, appends, f"A{start}", value_input_option="RAW")
            self._call(ws.columns_auto_resize, 0, len(header))  # cosmetic; retried like any call
        return {"updated": len(updates), "appended": len(appends)}
