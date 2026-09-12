"""SQLite-backed job registry.

Persists the job state machine:
    queued -> in_progress -> completed | failed | discarded
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

_DB_PATH: Path | None = None
_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    file_size     INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL,
    progress      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    completed_at  TEXT,
    error_message TEXT,
    temp_dir      TEXT,
    zip_path      TEXT,
    job_type      TEXT NOT NULL DEFAULT 'records',
    options_json  TEXT NOT NULL DEFAULT '{}',
    result_json   TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(path: Path) -> None:
    global _DB_PATH
    _DB_PATH = path
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(_SCHEMA)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        migrations = {
            "job_type": "ALTER TABLE jobs ADD COLUMN job_type TEXT NOT NULL DEFAULT 'records'",
            "options_json": "ALTER TABLE jobs ADD COLUMN options_json TEXT NOT NULL DEFAULT '{}'",
            "result_json": "ALTER TABLE jobs ADD COLUMN result_json TEXT",
        }
        for column, statement in migrations.items():
            if column not in columns:
                conn.execute(statement)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["filenames"] = json.loads(d["filename"])
    except (json.JSONDecodeError, TypeError):
        d["filenames"] = [d["filename"]]
    try:
        d["options"] = json.loads(d.get("options_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        d["options"] = {}
    try:
        d["result"] = json.loads(d.get("result_json") or "null")
    except (json.JSONDecodeError, TypeError):
        d["result"] = None
    return d


def create_job(
    job_id: str,
    filenames: list[str],
    file_size: int,
    temp_dir: str,
    job_type: str = "records",
    options: dict | None = None,
) -> dict:
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, filename, file_size, status, progress, created_at, temp_dir, job_type, options_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                json.dumps(filenames),
                file_size,
                "queued",
                0,
                _now(),
                temp_dir,
                job_type,
                json.dumps(options or {}),
            ),
        )
    return get_job(job_id)


def update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _LOCK, _connect() as conn:
        conn.execute(
            f"UPDATE jobs SET {cols} WHERE id = ?",
            (*fields.values(), job_id),
        )


def set_status(job_id: str, status: str, **fields) -> None:
    if status in ("completed", "failed", "discarded") and "completed_at" not in fields:
        fields["completed_at"] = _now()
    update_job(job_id, status=status, **fields)


def get_job(job_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_jobs() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
    return [_row_to_dict(r) for r in rows]
