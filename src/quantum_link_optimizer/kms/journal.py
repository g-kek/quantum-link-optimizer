import json
import time


class Journal:
    def __init__(self, path):
        self.file = path.open("x")

    def emit(self, event, **fields):
        self.file.write(
            json.dumps({"event": event, "time_ns": time.monotonic_ns(), **fields})
            + "\n"
        )
        self.file.flush()

    def close(self):
        self.file.close()
