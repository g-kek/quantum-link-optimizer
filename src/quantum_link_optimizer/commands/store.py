import hashlib
import json
import sqlite3
import time


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS commands (
                session TEXT, id TEXT, digest TEXT, result TEXT,
                PRIMARY KEY (session, id)
            );
            CREATE TABLE IF NOT EXISTS state (
                session TEXT PRIMARY KEY, sequence INTEGER, value INTEGER,
                executions INTEGER
            );
        """)

    def execute(self, session, body):
        required = {"id", "sequence", "value", "deadline_ns"}
        if set(body) != required:
            raise ValueError("invalid command fields")
        if not isinstance(body["id"], str) or not 1 <= len(body["id"]) <= 128:
            raise ValueError("invalid command ID")
        for field in ("sequence", "value", "deadline_ns"):
            if type(body[field]) is not int or not 0 <= body[field] < 2**63:
                raise ValueError(f"invalid {field}")
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        with self.db:
            row = self.db.execute(
                "SELECT digest, result FROM commands WHERE session=? AND id=?",
                (session, body["id"]),
            ).fetchone()
            if row:
                if row[0] != digest:
                    return {"id": body["id"], "status": "conflict"}
                return {**json.loads(row[1]), "duplicate": True}
            now = time.monotonic_ns()
            state = self.db.execute(
                "SELECT sequence FROM state WHERE session=?", (session,)
            ).fetchone()
            status = "executed"
            if now > body["deadline_ns"]:
                status = "expired"
            elif state and body["sequence"] <= state[0]:
                status = "stale"
            if status == "executed":
                self.db.execute(
                    "INSERT INTO state VALUES (?, ?, ?, 1) "
                    "ON CONFLICT(session) DO UPDATE SET sequence=excluded.sequence, "
                    "value=excluded.value, executions=executions+1",
                    (session, body["sequence"], body["value"]),
                )
            result = {
                "id": body["id"],
                "status": status,
                "duplicate": False,
                "executed_ns": now if status == "executed" else None,
            }
            self.db.execute(
                "INSERT INTO commands VALUES (?, ?, ?, ?)",
                (session, body["id"], digest, json.dumps(result)),
            )
            return result

    def close(self):
        self.db.close()
