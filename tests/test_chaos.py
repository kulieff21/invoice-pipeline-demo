"""Kill the pipeline again and again (SIGKILL mid-processing, hard exit right after a sink write)
and check that the final sheets equal a clean run: no lost rows, no duplicated rows."""

import subprocess
import sys
from pathlib import Path

from tests.test_pipeline import run, sheet
from invoice_pipeline.pipeline import INVOICE_SHEET, LINE_SHEET
from invoice_pipeline.sinks import FileSink

ROOT = Path(__file__).resolve().parents[1]


def _worker(inbox: Path, tmp: Path, crash_after: int) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "tests.chaos_worker", str(inbox), str(tmp / "chaos.sqlite"), str(tmp / "chaos"),
         str(crash_after)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def test_repeated_crashes_converge_to_the_clean_result(text_inbox, tmp_path):
    inbox = text_inbox / "inbox"
    run(inbox, tmp_path / "ref.sqlite", FileSink(tmp_path / "ref"))

    # phase 1: SIGKILL at arbitrary moments (mostly mid-extraction, sometimes mid-sync)
    kills = 0
    for delay in (2.0, 2.5, 3.0, 3.5, 4.5):
        proc = _worker(inbox, tmp_path, -1)
        try:
            proc.wait(timeout=delay)
            print(f"run  kill -9 after {delay}s   finished first (exit {proc.returncode})")
        except subprocess.TimeoutExpired:
            proc.kill()  # SIGKILL: no finally blocks, no flush
            proc.wait()
            kills += 1
            print(f"run  kill -9 after {delay}s   killed")

    # phase 2: die right after the N-th sink write is applied, before it is marked as synced
    crashes_after_write = 0
    for crash_after in (1, 2, 1, 3, 2, 5):
        proc = _worker(inbox, tmp_path, crash_after)
        proc.wait(timeout=120)
        crashes_after_write += proc.returncode == 137
        print(f"run  die after sheet write #{crash_after}   exit {proc.returncode}"
              + ("   (write applied, not recorded)" if proc.returncode == 137 else ""))
        assert proc.returncode in (0, 137), proc.stderr.read().decode()

    proc = _worker(inbox, tmp_path, -1)
    assert proc.wait(timeout=120) == 0, proc.stderr.read().decode()
    print("run  no fault                exit 0")

    assert kills >= 2 and crashes_after_write >= 4, (kills, crashes_after_write)
    for name in (INVOICE_SHEET, LINE_SHEET):
        got, ref = sheet(tmp_path / "chaos", name), sheet(tmp_path / "ref", name)
        assert got[0] == ref[0]
        assert sorted(got[1:]) == sorted(ref[1:]), name
        keys = [r[0] for r in got[1:]]
        assert len(keys) == len(set(keys))
        print(f"check {name}: {len(keys)} rows, {len(set(keys))} unique keys, equal to a clean run")
    print(f"kills={kills} crashes_after_write={crashes_after_write}")
