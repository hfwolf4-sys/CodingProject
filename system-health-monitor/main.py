"""
main.py - Entry point for the System Health Monitor.

One monitor cycle:
    1. Ping every host listed in config.py
    2. Log the results to the SQLite database
    3. Rebuild today's summary report (CSV and PDF)

Usage:
    python main.py                               # run one cycle
    python main.py --continuous                  # run a cycle every CHECK_INTERVAL_MINUTES until Ctrl+C
    python main.py --continuous --interval 1     # run a cycle every minute
    python main.py --continuous --cycles 12      # stop after 12 cycles
    python main.py --report-only --date 2026-11-25   # rebuild an earlier day's report
"""

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime

import config
import database
import pinger
import report

log = logging.getLogger("health_monitor")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Ping a list of hosts, log the results to SQLite, and build a daily health report.")
    parser.add_argument("--continuous", action="store_true",
                        help="keep running a cycle every --interval minutes until stopped with Ctrl+C")
    parser.add_argument("--interval", type=float, default=config.CHECK_INTERVAL_MINUTES, metavar="N",
                        help=f"minutes between cycles in continuous mode (default {config.CHECK_INTERVAL_MINUTES})")
    parser.add_argument("--cycles", type=int, metavar="N",
                        help="in continuous mode, stop after N cycles (default: run until Ctrl+C)")
    parser.add_argument("--report-only", action="store_true",
                        help="skip pinging and rebuild the report from the database")
    parser.add_argument("--date", metavar="YYYY-MM-DD",
                        help="day to report on with --report-only (default: today)")
    args = parser.parse_args()

    if args.interval <= 0:
        parser.error("--interval must be greater than 0")
    if args.cycles is not None and args.cycles <= 0:
        parser.error("--cycles must be greater than 0")
    if args.report_only and args.continuous:
        parser.error("--report-only and --continuous cannot be used together")
    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            parser.error("--date must look like YYYY-MM-DD")
    return args


def setup_logging():
    """Log to the console, and to LOG_FILE if one is set in config.py."""
    handlers = [logging.StreamHandler(sys.stdout)]
    if config.LOG_FILE:
        try:
            folder = os.path.dirname(config.LOG_FILE)
            if folder:
                os.makedirs(folder, exist_ok=True)
            handlers.append(logging.FileHandler(config.LOG_FILE, encoding="utf-8"))
        except OSError as exc:
            print(f"Warning: cannot write log file {config.LOG_FILE} ({exc}); logging to console only")
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")


def check_hosts():
    """Ping every host, save the results, and return True if they were saved."""
    hosts = pinger.normalize_hosts(config.HOSTS)
    if not hosts:
        log.error("No hosts configured. Add hostnames or IP addresses to HOSTS in config.py.")
        return False

    run_id = database.start_run()
    try:
        log.info("Pinging %d hosts (%d packets each, %ss timeout)...",
                 len(hosts), config.PING_COUNT, config.PING_TIMEOUT_SECONDS)
        results = pinger.ping_all(hosts, config.PING_COUNT, config.PING_TIMEOUT_SECONDS,
                                  config.MAX_PARALLEL_PINGS)

        for r in results:
            if r.is_online:
                log.info("  %-24s ONLINE   %8.2f ms   loss %5.1f%%", r.host, r.avg_response_ms, r.packet_loss_pct)
            else:
                log.warning("  %-24s OFFLINE  %s", r.host, r.error)

        saved = database.insert_results(run_id, results)
        online = sum(r.is_online for r in results)
        database.finish_run(run_id, "SUCCESS", len(results), online)
        log.info("Saved %d results. %d of %d hosts online.", saved, online, len(results))
        return True
    except Exception as exc:
        # Record the failed cycle, then let main() decide what to do.
        try:
            database.finish_run(run_id, "FAILED", error=str(exc))
        except database.DatabaseError:
            pass
        raise


def build_report(day):
    """Generate the CSV and PDF report for a day. Returns True on success."""
    files = report.generate_report(day)
    if not files:
        log.warning("No results found for %s, so no report was written.", day)
        return False
    for path in files:
        log.info("Report written: %s", path)
    return True


def run_cycle():
    """One full cycle: ping, save, report. Returns True if every step worked."""
    if not check_hosts():
        return False
    return build_report(date.today().isoformat())


def main():
    args = parse_args()
    setup_logging()

    try:
        database.init_db()
        log.info("Database ready at %s", config.DATABASE_PATH)

        if args.report_only:
            return 0 if build_report(args.date or date.today().isoformat()) else 1

        if not args.continuous:
            return 0 if run_cycle() else 1

        # Continuous mode: keep going even if one cycle fails.
        log.info("Continuous mode: one cycle every %g minute(s). Press Ctrl+C to stop.", args.interval)
        cycle = 0
        while True:
            cycle += 1
            started = time.monotonic()
            log.info("--- Cycle %d ---", cycle)
            try:
                run_cycle()
            except (database.DatabaseError, report.ReportError) as exc:
                log.error("Cycle %d failed: %s (will retry next cycle)", cycle, exc)

            if args.cycles and cycle >= args.cycles:
                log.info("Finished %d cycles.", cycle)
                return 0
            # Sleep for what is left of the interval, so cycles stay evenly spaced.
            wait = max(0.0, args.interval * 60 - (time.monotonic() - started))
            log.info("Next cycle in %.0f seconds.", wait)
            time.sleep(wait)

    except KeyboardInterrupt:
        log.info("Stopped by user.")
        return 0
    except database.DatabaseError as exc:
        log.error("%s", exc)
        return 1
    except report.ReportError as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
