"""MLflow table meanings; SQLite mechanics remain in the generic connector."""
from dmux.sqlite_connector import SQLiteReader


class SQLiteStore:
    def __init__(self):
        self.reader = SQLiteReader()

    def status(self, database, run_id):
        rows = self.reader.read(database, table="runs", columns=["status"], where={"run_uuid": run_id}, limit=2)
        if len(rows) != 1 or rows[0]["status"] not in {"RUNNING", "SCHEDULED", "FINISHED", "FAILED", "KILLED"}:
            raise ValueError("SQLite run missing, duplicated, or has an invalid status")
        return rows[0]["status"]

    def value(self, database, run_id, kind, key):
        rows = self.reader.read(database, table=kind, columns=["value"], where={"run_uuid": run_id, "key": key}, limit=2)
        if len(rows) != 1 or not isinstance(rows[0]["value"], str):
            raise ValueError(f"SQLite {kind}/{key} missing, duplicated, or not text")
        return rows[0]["value"].strip()
