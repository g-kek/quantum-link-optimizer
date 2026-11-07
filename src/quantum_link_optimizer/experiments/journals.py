"""Deferred I/O retains serialization and exports the same event contract."""

import io

from quantum_link_optimizer.telemetry_lab.events import EventLog


class DeferredJournal(EventLog):
    def __init__(self, path, source):
        super().__init__(path, source)
        self.destination = self.file
        self.file = io.StringIO()

    def emit(self, event, **fields):
        if self.file.tell() > 64 * 1024**2:
            raise BufferError("bounded experiment event buffer is full")

        super().emit(event, **fields)

    def close(self):
        self.destination.write(self.file.getvalue())
        self.destination.close()
        self.file.close()
