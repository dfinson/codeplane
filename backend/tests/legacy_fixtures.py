"""Application-owned historical-data fixtures captured from transition release 287483a7."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace


def seed_legacy_store(path: Path, *, job_id: str = "legacy-job", grants: bool = True, rows: bool = True):
    captured = json.loads((Path(__file__).parent / "fixtures/retired_governance.json").read_text())
    connection = sqlite3.connect(path)
    with connection:
        for statement in captured["schema"]:
            connection.execute(statement.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1))
        if not rows:
            connection.execute("INSERT INTO alembic_version VALUES(?)", ("0001_initial",))
        if rows:
            now = datetime.now(UTC).isoformat()
            for table, records in captured["tables"].items():
                if table == "trust_grants" and not grants:
                    continue
                for original in records:
                    record = dict(original)
                    if "session_id" in record:
                        record["session_id"] = job_id
                    for column in ("granted_at", "updated_at", "processed_at"):
                        if column in record:
                            record[column] = now
                    columns = ",".join(record)
                    placeholders = ",".join("?" for _ in record)
                    connection.execute(f"INSERT INTO {table}({columns}) VALUES({placeholders})", tuple(record.values()))
    return SimpleNamespace(
        _store=SimpleNamespace(connection=connection, write_transaction=lambda: connection),
        close=connection.close,
    )
