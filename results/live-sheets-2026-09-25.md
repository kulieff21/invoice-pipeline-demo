# Live Google Sheets run, 2026-09-25

Service account, one spreadsheet, dev inbox (124 files: 81 text-layer, 43 scans incl. 4 re-sent copies).

| Step | Result |
|---|---|
| First run | 124 processed; `invoices` 120 rows appended, `line_items` 406 rows appended |
| Second run, same inbox | 0 processed (124 already seen), 0 rows written |
| Outbox reset (simulates a crash after every write, before it was recorded) + sync through a fault injector at 65 % failure per call (15 % 429, 10 % 503, 40 % write-applied-then-timeout) | Gave up after 7 attempts on one batch, as designed; 4 of the failed calls had written to the sheet. Sheet afterwards: 120 / 406 rows, all keys unique |
| Same, at 40 % (10 % 429, 5 % 503, 25 % write-applied-then-timeout) | 6 injected failures, 6 retries; 120 + 406 rows **updated, 0 appended**; nothing left pending; 120 / 406 unique keys |

The fault injector wraps the real `SheetsSink`: every write it lets through, or applies and then
reports as a timeout, hits the Google Sheets API.
