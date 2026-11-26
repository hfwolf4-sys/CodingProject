"""
database.py - SQLite storage for the System Health Monitor.

Handles three jobs:
    1. Creating the schema (tables and an index) if it does not exist yet
    2. Inserting ping results and a record of every monitor cycle
    3. Summary queries used by the report (per-host daily stats and raw checks)

Every sqlite3 error is re-raised as DatabaseError with a readable message,
so main.py can handle all database problems in one place.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

import config


class DatabaseError(Exception):
    """Raised when the database cannot be opened, read, or written."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS ping_results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL,
    host             TEXT    NOT NULL,
    label            TEXT    NOT NULL,
    timestamp        TEXT    NOT NULL,   -- local time, YYYY-MM-DD HH:MM:SS
    is_online        INTEGER NOT NULL,   -- 1 = replied, 0 = no reply
    avg_response_ms  REAL,               -- NULL when offline
    min_response_ms  REAL,
    max_response_ms  REAL,
    packets_sent     INTEGER NOT NULL,
    packets_received INTEGER NOT NULL,
    packet_loss_pct  REAL    NOT NULL,
    error            TEXT
);

CREATE INDEX IF NOT EXISTS idx_ping_results_day_host
    ON ping_results (timestamp, host);

CREATE TABLE IF NOT EXISTS monitor_runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    hosts_checked INTEGER DEFAULT 0,
    hosts_online  INTEGER DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'RUNNING',   -- RUNNING, SUCCESS, FAILED
    error         TEXT
);
"""


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def get_connection(db_path=None):
    """
    Open a connection, commit if the block succeeds, roll back if it fails,
    and always close it. sqlite3 errors are converted to DatabaseError.
    """
    db_path = db_path or config.DATABASE_PATH
    conn = None
    try:
        folder = os.path.dirname(db_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    except sqlite3.Error as exc:
        if conn is not None:
            conn.rollback()
        raise DatabaseError(f"Database error ({db_path}): {exc}") from exc
    except OSError as exc:
        raise DatabaseError(f"Could not create the database folder for {db_path}: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()


def init_db(db_path=None):
    """Create the tables and index if they do not exist yet."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Inserts
# ---------------------------------------------------------------------------

def start_run(db_path=None):
    """Record the start of a monitor cycle and return its run_id."""
    with get_connection(db_path) as conn:
        cursor = conn.execute("INSERT INTO monitor_runs (started_at) VALUES (?)", (_now(),))
        return cursor.lastrowid


def finish_run(run_id, status, hosts_checked=0, hosts_online=0, error=None, db_path=None):
    """Mark a monitor cycle as SUCCESS or FAILED."""
    with get_connection(db_path) as conn:
        conn.execute(
            """UPDATE monitor_runs
               SET finished_at = ?, status = ?, hosts_checked = ?, hosts_online = ?, error = ?
               WHERE run_id = ?""",
            (_now(), status, hosts_checked, hosts_online, error, run_id),
        )


def insert_results(run_id, results, db_path=None):
    """
    Save a list of PingResult objects in one transaction.
    If any row fails, none are saved, so a cycle is never half-recorded.
    """
    rows = [
        (run_id, r.host, r.label, r.timestamp, int(r.is_online),
         r.avg_response_ms, r.min_response_ms, r.max_response_ms,
         r.packets_sent, r.packets_received, r.packet_loss_pct, r.error)
        for r in results
    ]
    with get_connection(db_path) as conn:
        conn.executemany(
            """INSERT INTO ping_results
               (run_id, host, label, timestamp, is_online,
                avg_response_ms, min_response_ms, max_response_ms,
                packets_sent, packets_received, packet_loss_pct, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    return len(rows)


# ---------------------------------------------------------------------------
# Summary queries (used by report.py)
# ---------------------------------------------------------------------------

DAILY_SUMMARY_SQL = """
WITH day AS (
    SELECT * FROM ping_results WHERE timestamp >= ? AND timestamp < ?
),
latest AS (
    -- the most recent check of each host, which gives its current status
    SELECT host, is_online AS current_online, error AS last_error, timestamp AS last_check
    FROM (
        SELECT host, is_online, error, timestamp,
               ROW_NUMBER() OVER (PARTITION BY host ORDER BY timestamp DESC, id DESC) AS rn
        FROM day
    )
    WHERE rn = 1
)
SELECT d.host,
       MAX(d.label)                                   AS label,
       COUNT(*)                                       AS checks,
       SUM(d.is_online)                               AS online_checks,
       ROUND(100.0 * SUM(d.is_online) / COUNT(*), 2)  AS uptime_pct,
       ROUND(AVG(d.avg_response_ms), 2)               AS avg_response_ms,
       ROUND(MIN(d.min_response_ms), 2)               AS min_response_ms,
       ROUND(MAX(d.max_response_ms), 2)               AS max_response_ms,
       ROUND(AVG(d.packet_loss_pct), 2)               AS avg_packet_loss_pct,
       MIN(d.timestamp)                               AS first_check,
       l.last_check,
       l.current_online,
       l.last_error
FROM day d
JOIN latest l ON l.host = d.host
GROUP BY d.host
ORDER BY MIN(d.id)
"""


def _day_bounds(day):
    """Return the start and end timestamps of a day given as 'YYYY-MM-DD'."""
    start = datetime.strptime(day, "%Y-%m-%d")
    next_day = start + timedelta(days=1)
    # Queries use "timestamp >= start AND timestamp < next midnight"
    return start.strftime("%Y-%m-%d %H:%M:%S"), next_day.strftime("%Y-%m-%d %H:%M:%S")


def get_daily_summary(day, db_path=None):
    """Per-host stats for one day (list of dicts), calculated in SQL."""
    start, end = _day_bounds(day)
    with get_connection(db_path) as conn:
        return [dict(row) for row in conn.execute(DAILY_SUMMARY_SQL, (start, end))]


def get_results_for_day(day, db_path=None):
    """Every individual check for one day (list of dicts), oldest first."""
    start, end = _day_bounds(day)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT run_id, host, label, timestamp, is_online, avg_response_ms,
                      packets_sent, packets_received, packet_loss_pct, error
               FROM ping_results
               WHERE timestamp >= ? AND timestamp < ?
               ORDER BY timestamp, id""",
            (start, end),
        )
        return [dict(row) for row in rows]
