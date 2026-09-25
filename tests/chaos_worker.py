"""Run the pipeline in a separate process that may crash on purpose.

    python -m tests.chaos_worker <inbox> <state.sqlite> <sink-dir> <crash-after-writes|-1>

With crash-after-writes = N the process dies (os._exit, no cleanup, like SIGKILL) right after
the N-th sink write has been applied and before the pipeline can mark those rows as synced:
the worst moment for a naive "append then remember" sync.
"""

import os
import sys
from datetime import date
from pathlib import Path

from invoice_pipeline.pipeline import Pipeline
from invoice_pipeline.sinks import FileSink
from invoice_pipeline.state import State


class CrashingSink(FileSink):
    def __init__(self, folder, crash_after: int):
        super().__init__(folder)
        self.crash_after = crash_after
        self.writes = 0

    def upsert(self, sheet, header, rows):
        result = super().upsert(sheet, header, rows)
        self.writes += 1
        if self.writes == self.crash_after:
            os._exit(137)
        return result


def main() -> None:
    inbox, state_path, sink_dir, crash_after = sys.argv[1:5]
    state = State(state_path)
    Pipeline(state, CrashingSink(sink_dir, int(crash_after)), as_of=date(2026, 9, 25), batch_size=7,
             log=lambda _: None).run(Path(inbox))
    state.close()


if __name__ == "__main__":
    main()
