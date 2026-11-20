"""
database.py - Stores weather records in a local SQLite database and reads them back.

Tables:
    weather_daily - one row per location per day (re-running updates rows, no duplicates)
    pull_log      - one row per pipeline run, useful for auditing and troubleshooting
"""

import logging
import os
import sqlite3
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
    """Create the tables if they do not exist yet."""
    # One REAL column for each weather variable listed in config.py
    variable_columns = ",\n".join(f"    {_safe_name(v)} REAL" for v in config.DAILY_VARIABLES)

    weather_table = f"""
        CREATE TABLE IF NOT EXISTS weather_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            date TEXT NOT NULL,
            {variable_columns},
            pulled_at TEXT NOT NULL,
            UNIQUE (location, date)
        )
    """
    log_table = """
        CREATE TABLE IF NOT EXISTS pull_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL,
            location TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            rows_saved INTEGER NOT NULL,
            status TEXT NOT NULL,
            message TEXT
        )
    """
    try:
        with get_connection() as conn:
            conn.execute(weather_table)
            conn.execute(log_table)
            _add_missing_columns(conn)
        logger.info("Database ready at %s", config.DATABASE_PATH)
    except sqlite3.Error as exc:
        raise DatabaseError(f"Could not create database tables: {exc}") from exc


def save_records(records):
    """Insert or update daily weather rows. Returns the number of rows saved."""
    if not records:
        return 0

    columns = ["location", "latitude", "longitude", "date"]
    columns += [_safe_name(v) for v in config.DAILY_VARIABLES]
    columns.append("pulled_at")

    placeholders = ", ".join("?" for _ in columns)
    # On a repeat run, overwrite the existing row for that location/date
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns[4:])
    sql = (
        f"INSERT INTO weather_daily ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT (location, date) DO UPDATE SET {updates}"
    )

    pulled_at = datetime.now().isoformat(timespec="seconds")
    rows = [
        (
            config.LOCATION_NAME,
            config.LATITUDE,
            config.LONGITUDE,
            record["date"],
            *[record.get(v) for v in config.DAILY_VARIABLES],
            pulled_at,
        )
        for record in records
    ]

    try:
        with get_connection() as conn:  # commits on success, rolls back on error
            conn.executemany(sql, rows)
        logger.info("Saved %d rows to weather_daily", len(rows))
        return len(rows)
    except sqlite3.Error as exc:
        raise DatabaseError(f"Could not save weather records: {exc}") from exc


def log_run(start_date, end_date, rows_saved, status, message=""):
    """Record the outcome of a pipeline run in pull_log."""
    sql = """
        INSERT INTO pull_log (run_at, location, start_date, end_date, rows_saved, status, message)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    try:
        with get_connection() as conn:
            conn.execute(
                sql,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    config.LOCATION_NAME,
                    start_date,
                    end_date,
                    rows_saved,
                    status,
                    message,
                ),
            )
    except (sqlite3.Error, DatabaseError) as exc:
        # Logging the run should never crash the pipeline, so only warn.
        logger.warning("Could not write to pull_log: %s", exc)


def load_weather(start_date, end_date):
    """Return the stored rows for the configured location and dates as a DataFrame."""
    sql = """
        SELECT * FROM weather_daily
        WHERE location = ? AND date BETWEEN ? AND ?
        ORDER BY date
    """
    try:
        with get_connection() as conn:
            df = pd.read_sql_query(sql, conn, params=(config.LOCATION_NAME, start_date, end_date))
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise DatabaseError(f"Could not read weather data: {exc}") from exc

    df["date"] = pd.to_datetime(df["date"])
    return df


def _add_missing_columns(conn):
    """If a new variable was added to config.py, add its column to an existing table."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(weather_daily)")}
    for variable in config.DAILY_VARIABLES:
        name = _safe_name(variable)
        if name not in existing:
            conn.execute(f"ALTER TABLE weather_daily ADD COLUMN {name} REAL")
            logger.info("Added new column %s to weather_daily", name)


def _safe_name(name):
    """Only allow letters, numbers, and underscores in column names (guards the SQL)."""
    if not name.replace("_", "").isalnum():
        raise DatabaseError(f"Invalid variable name in config.DAILY_VARIABLES: {name!r}")
    return name
