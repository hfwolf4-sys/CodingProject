"""
main.py - Entry point for the REST API Data Puller.

Runs the full pipeline:
    1. Pull daily weather data from the Open-Meteo API
    2. Save it to a local SQLite database
    3. Generate a summary report (PDF and/or CSV)

Usage:
    python main.py                  # full pipeline (pull, store, report)
    python main.py --report-only    # skip the API call, report on data already stored
    python main.py --format csv     # override REPORT_FORMAT from config.py for this run
"""

import argparse
import logging
import sys

import api_client
import config
import database
import report


def parse_args():
    parser = argparse.ArgumentParser(description="Pull weather data, store it, and report on it.")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="skip the API call and build the report from data already in the database",
    )
    parser.add_argument(
        "--format",
        choices=["pdf", "csv", "both"],
        help="report format for this run (default comes from config.py)",
    )
    return parser.parse_args()


def setup_logging():
    """Print timestamped progress messages to the console."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )


def run_pipeline(report_only=False):
    """Run each step in order. Returns 0 on success, 1 on failure (used as exit code)."""
    log = logging.getLogger("pipeline")
    start_date, end_date = config.get_date_range()
    log.info("Location: %s | Dates: %s to %s", config.LOCATION_NAME, start_date, end_date)

    # Step 1: make sure the database and tables exist
    try:
        database.initialize_database()
    except database.DatabaseError as exc:
        log.error("Database setup failed: %s", exc)
        return 1

    # Step 2: pull from the API and store the results
    if not report_only:
        try:
            payload = api_client.fetch_weather(start_date, end_date)
            records = api_client.parse_daily_records(payload)
            saved = database.save_records(records)
            database.log_run(start_date, end_date, saved, "SUCCESS")
        except api_client.APIError as exc:
            log.error("API step failed: %s", exc)
            database.log_run(start_date, end_date, 0, "FAILED", str(exc))
            return 1
        except database.DatabaseError as exc:
            log.error("Saving data failed: %s", exc)
            database.log_run(start_date, end_date, 0, "FAILED", str(exc))
            return 1

    # Step 3: read the data back and build the report
    try:
        df = database.load_weather(start_date, end_date)
        files = report.generate_reports(df, start_date, end_date)
    except database.DatabaseError as exc:
        log.error("Reading data failed: %s", exc)
        return 1
    except (ValueError, OSError) as exc:
        log.error("Report generation failed: %s", exc)
        return 1

    log.info("Done. %d rows in report. Files created:", len(df))
    for path in files:
        log.info("  %s", path)
    return 0


def main():
    args = parse_args()
    setup_logging()
    if args.format:
        config.REPORT_FORMAT = args.format  # command-line choice wins for this run
    sys.exit(run_pipeline(report_only=args.report_only))


if __name__ == "__main__":
    main()
