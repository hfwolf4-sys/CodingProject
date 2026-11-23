"""
database.py - Stores ping results in a local SQLite database and reads them back.

Tables:
    ping_results  - one row per host per check (the raw monitoring history)
    monitor_runs  - one row per monitor cycle, useful for auditing and troubleshooting
"""

import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime

import pandas as pd

import config

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    """Raised when the database cannot be opened, written, or read."""


def get_connection():
    """Open a connection to the SQLite file, creating its folder if needed."""
    try:
        folder = os.path.dirname(config.DATABASE_PATH)
        if folder:
            os.makedirs(folder, exist_ok=True)
        return sqlite3.connect(config.DATABASE_PATH)
    except (sqlite3.Error, OSError) as exc:
        raise DatabaseError(f"Could not open database at {config.DATABASE_PATH}: {exc}") from exc


def initialize_database():
    """Create the tables and index if they do not exist yet."""
    results_table = """
        CREATE TABLE IF NOT EXISTS ping_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            host TEXT NOT NULL,
            timestamp TEXT NOT NULL,          -- local time, YYYY-MM-DD HH:MM:SS
            is_up INTEGER NOT NULL,           -- 1 = responded, 0 = no response
            response_time_ms REAL,            -- average round trip, NULL if offline
            packets_sent INTEGER NOT NULL,
            packets_received INTEGER NOT NULL,
            packet_loss REAL NOT NULL,        -- percent
            method TEXT NOT NULL,             -- icmplib or subprocess
            error TEXT,
            FOREIGN KEY (run_id) REFERENCES monitor_runs (id)
        )
    """
    runs_table = """
        CREATE TABLE IF NOT EXISTS monitor_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            hosts_checked INTEGER NOT NULL DEFAULT 0,
            hosts_up INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,             -- RUNNING, SUCCESS, or FAILED
            error_message TEXT
        )
    """
    # Reports filter by day and host, so index those columns.
    index = "CREATE INDEX IF NOT EXISTS idx_results_time_host ON ping_results (timestamp, host)"

    try:
        with closing(get_connection()) as conn, conn:
            conn.execute(runs_table)
            conn.execute(results_table)
            conn.execute(index)
    except sqlite3.Error as exc:
        raise DatabaseError(f"Could not create tables: {exc}") from exc
    logger.info("Database ready at %s", config.DATABASE_PATH)


def start_run():
    """Record the start of a monitor cycle and return its run id."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with closing(get_connection()) as conn, conn:
            cursor = conn.execute(
                "INSERT INTO monitor_runs (started_at, status) VALUES (?, 'RUNNING')", (now,)
            )
            return cursor.lastrowid
    except sqlite3.Error as exc:
        raise DatabaseError(f"Could not record run start: {exc}") from exc


def finish_run(run_id, hosts_checked, hosts_up, status, error_message=None):
    """Update a monitor cycle row with its outcome. Logs instead of raising on failure."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with closing(get_connection()) as conn, conn:
            conn.execute(
                """UPDATE monitor_runs
                   SET finished_at = ?, hosts_checked = ?, hosts_up = ?, status = ?, error_message = ?
                   WHERE id = ?""",
                (now, hosts_checked, hosts_up, status, error_message, run_id),
            )
    except (sqlite3.Error, DatabaseError) as exc:
        # Losing an audit row should not hide the real result, so just warn.
        logger.warning("Could not update monitor_runs for run %s: %s", run_id, exc)


def save_results(results, run_id=None):
    """Insert a list of PingResult objects in one transaction. Returns rows saved."""
    if not results:
        return 0
    rows = [
        (
            run_id,
            r.host,
            r.timestamp,
            int(r.is_up),
            r.response_time_ms,
            r.packets_sent,
            r.packets_received,
            r.packet_loss,
            r.method,
            r.error,
        )
        for r in results
    ]
    try:
        # "with conn" commits on success and rolls back if anything fails,
        # so a cycle is saved completely or not at all.
        with closing(get_connection()) as conn, conn:
            conn.executemany(
                """INSERT INTO ping_results
                   (run_id, host, timestamp, is_up, response_time_ms,
                    packets_sent, packets_received, packet_loss, method, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
    except sqlite3.Error as exc:
        raise DatabaseError(f"Could not save ping results: {exc}") from exc
    logger.info("Saved %d results to ping_results", len(rows))
    return len(rows)


def load_results_for_day(day):
    """Return every ping result for one day ("YYYY-MM-DD") as a pandas DataFrame."""
    query = """
        SELECT host, timestamp, is_up, response_time_ms, packets_sent,
               packets_received, packet_loss, method, error
        FROM ping_results
        WHERE timestamp >= ? AND timestamp < date(?, '+1 day')
        ORDER BY timestamp, host
    """
    try:
        with closing(get_connection()) as conn:
            df = pd.read_sql_query(query, conn, params=(day, day))
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise DatabaseError(f"Could not read ping results: {exc}") from exc

    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["is_up"] = df["is_up"].astype(bool)
    return df
