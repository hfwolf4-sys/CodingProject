"""
main.py - Entry point for the Network Uptime Monitor.

Runs the full monitor cycle:
    1. Ping every host listed in config.py
    2. Save the results to a local SQLite database
    3. Generate today's summary report (CSV and/or PDF)

Usage:
    python main.py                          # one check of every host, then the report
    python main.py --continuous             # check every CHECK_INTERVAL_MINUTES until Ctrl+C
    python main.py --continuous --interval 1 --cycles 10
                                            # check every minute, stop after 10 checks
    python main.py --report-only            # skip pinging, rebuild today's report
    python main.py --report-only --date 2026-11-20
                                            # rebuild the report for an earlier day
    python main.py --format csv             # override REPORT_FORMAT for this run
"""

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta

import config
import database
import pinger
import report


def parse_args():
    parser = argparse.ArgumentParser(description="Ping a list of hosts, log the results, and report on uptime.")
    parser.add_argument("--continuous", action="store_true",
                        help="keep checking every --interval minutes until stopped with Ctrl+C")
    parser.add_argument("--interval", type=float, metavar="MINUTES",
                        help=f"minutes between checks in continuous mode (default {config.CHECK_INTERVAL_MINUTES})")
    parser.add_argument("--cycles", type=int, metavar="N",
                        help="in continuous mode, stop after N checks (default: run until Ctrl+C)")
    parser.add_argument("--report-only", action="store_true",
                        help="skip pinging and build the report from data already in the database")
    parser.add_argument("--date", metavar="YYYY-MM-DD",
                        help="day to report on (default: today); used with --report-only")
    parser.add_argument("--format", choices=["pdf", "csv", "both"],
                        help="report format for this run (default comes from config.py)")
    args = parser.parse_args()

    if args.interval is not None and args.interval <= 0:
        parser.error("--interval must be greater than 0")
    if args.cycles is not None and args.cycles <= 0:
        parser.error("--cycles must be greater than 0")
    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            parser.error("--date must look like YYYY-MM-DD")
    if args.report_only and args.continuous:
        parser.error("--report-only and --continuous cannot be used together")
    return args


def setup_logging():
    """Print timestamped progress messages to the console."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )


def run_checks(log):
    """Ping every host and save the results. Returns True if the results were saved."""
    hosts = list(dict.fromkeys(h.strip() for h in config.HOSTS if h.strip()))  # drop blanks/duplicates
    if not hosts:
        log.error("No hosts configured. Add hostnames or IP addresses to HOSTS in config.py.")
        return False

    try:
        run_id = database.start_run()
    except database.DatabaseError as exc:
        log.error("Database error: %s", exc)
        return False

    log.info("Pinging %d hosts (%d packets each, %ss timeout)...",
             len(hosts), config.PING_COUNT, config.PING_TIMEOUT_SECONDS)
    results = pinger.ping_all(hosts)

    # Print one line per host so progress is visible in the console.
    for r in results:
        if r.is_up:
            log.info("  %-24s ONLINE   %8.2f ms   loss %5.1f%%", r.host, r.response_time_ms, r.packet_loss)
        else:
            log.warning("  %-24s OFFLINE  %s", r.host, r.error)

    hosts_up = sum(r.is_up for r in results)
    try:
        database.save_results(results, run_id)
    except database.DatabaseError as exc:
        log.error("Saving results failed: %s", exc)
        database.finish_run(run_id, len(results), hosts_up, "FAILED", str(exc))
        return False

    database.finish_run(run_id, len(results), hosts_up, "SUCCESS")
    log.info("%d of %d hosts online", hosts_up, len(results))
    return True


def build_report(log, day):
    """Load one day's results and write the reports. Returns True on success."""
    try:
        df = database.load_results_for_day(day)
        files = report.generate_reports(df, day)
    except database.DatabaseError as exc:
        log.error("Reading results failed: %s", exc)
        return False
    except report.ReportError as exc:
        log.error("Report generation failed: %s", exc)
        return False
    except Exception as exc:  # noqa: BLE001 - e.g. an fpdf2 layout error; keep monitoring running
        log.exception("Unexpected error while building the report: %s", exc)
        return False

    log.info("Report covers %d checks for %s", len(df), day)
    return bool(files)


def run_once(log, report_only=False, day=None):
    """One full cycle: ping, save, report. Returns an exit code (0 = success)."""
    day = day or date.today().isoformat()
    if not report_only and not run_checks(log):
        return 1
    return 0 if build_report(log, day) else 1


def run_continuous(log, interval_minutes, max_cycles=None):
    """Repeat the cycle every interval_minutes until Ctrl+C (or max_cycles is reached)."""
    log.info("Continuous mode: checking every %s minute(s). Press Ctrl+C to stop.", interval_minutes)
    cycle = 0
    failures = 0
    try:
        while True:
            cycle += 1
            started = time.monotonic()
            log.info("----- Cycle %d -----", cycle)

            # A failed cycle is logged and the monitor keeps going; one bad
            # cycle (e.g. a locked database) should not stop all monitoring.
            if run_once(log) != 0:
                failures += 1
                log.warning("Cycle %d had errors (%d so far). Will try again next cycle.", cycle, failures)

            if max_cycles and cycle >= max_cycles:
                log.info("Reached %d cycles, stopping.", max_cycles)
                break

            # Wait for the rest of the interval, counting the time the cycle took.
            wait = max(0.0, interval_minutes * 60 - (time.monotonic() - started))
            next_run = datetime.now() + timedelta(seconds=wait)
            log.info("Next check at %s", next_run.strftime("%H:%M:%S"))
            time.sleep(wait)
    except KeyboardInterrupt:
        log.info("Stopped by user after %d cycle(s).", cycle)

    return 0 if failures == 0 else 1


def main():
    args = parse_args()
    setup_logging()
    log = logging.getLogger("monitor")
    if args.format:
        config.REPORT_FORMAT = args.format  # command-line choice wins for this run

    # Make sure the database and tables exist before anything else.
    try:
        database.initialize_database()
    except database.DatabaseError as exc:
        log.error("Database setup failed: %s", exc)
        sys.exit(1)

    if args.continuous:
        interval = args.interval if args.interval is not None else config.CHECK_INTERVAL_MINUTES
        sys.exit(run_continuous(log, interval, args.cycles))
    sys.exit(run_once(log, report_only=args.report_only, day=args.date))


if __name__ == "__main__":
    main()
