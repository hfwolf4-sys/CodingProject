"""
demo_data.py - Builds a sample report from a simulated day of checks.

A real day of monitoring takes 24 hours to collect. This script fills a
separate demo database with one simulated check every 5 minutes for a full
day, then builds the normal CSV and PDF report from it, so you can see what
the report looks like straight away. It does not touch your real database.

Usage:
    python demo_data.py                     # simulate yesterday
    python demo_data.py --date 2026-11-25   # simulate a specific day
Output goes to output/demo/.
"""

import argparse
import os
import random
from datetime import date, datetime, timedelta

import database
import report
from pinger import PingResult

DEMO_DB = "data/demo_health.db"
DEMO_OUTPUT = os.path.join("output", "demo")
PACKETS = 4

# host, label, typical ms, ms variation, chance a check fails, scheduled outage (start, end) or None
DEMO_HOSTS = [
    ("127.0.0.1", "Local machine", 0.04, 0.02, 0.0, None),
    ("192.168.1.1", "Home router", 2.5, 1.5, 0.002, ("12:30", "13:00")),
    ("8.8.8.8", "Google Public DNS", 14.0, 4.0, 0.0, None),
    ("1.1.1.1", "Cloudflare DNS", 11.0, 3.0, 0.0, None),
    ("github.com", "GitHub", 22.0, 6.0, 0.004, None),
    ("vpn.example.net", "Office VPN gateway", 185.0, 40.0, 0.01, None),
    ("10.255.255.1", "Offline example", None, None, 1.0, None),
    ("server.invalid", "DNS failure example", None, None, 1.0, None),
]


def simulate_check(host, label, base_ms, spread, fail_chance, outage, when, rng):
    """Make one PingResult that looks like a real check of this host."""
    timestamp = when.strftime("%Y-%m-%d %H:%M:%S")
    in_outage = outage and outage[0] <= when.strftime("%H:%M") < outage[1]

    if base_ms is None or in_outage or rng.random() < fail_chance:
        error = ("DNS lookup failed (host name could not be resolved)" if host.endswith(".invalid")
                 else "Request timed out (no reply)")
        return PingResult(host, label, timestamp, False, None, None, None, PACKETS, 0, 100.0, error)

    # Occasionally lose one packet, and add a little noise to each reply time
    received = PACKETS - (1 if rng.random() < 0.02 else 0)
    times = [max(0.01, rng.gauss(base_ms, spread / 2)) for _ in range(received)]
    if rng.random() < 0.01:                       # rare latency spike
        times[-1] *= 4
    return PingResult(host, label, timestamp, True,
                      round(sum(times) / received, 3), round(min(times), 3), round(max(times), 3),
                      PACKETS, received, round((PACKETS - received) / PACKETS * 100, 1), None)


def main():
    parser = argparse.ArgumentParser(description="Build a sample report from a simulated day of checks.")
    parser.add_argument("--date", default=(date.today() - timedelta(days=1)).isoformat(), metavar="YYYY-MM-DD")
    parser.add_argument("--seed", type=int, default=42, help="random seed, so the sample is repeatable")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    day_start = datetime.strptime(args.date, "%Y-%m-%d")

    if os.path.exists(DEMO_DB):
        os.remove(DEMO_DB)                        # start fresh every time
    database.init_db(DEMO_DB)

    for i in range(288):                          # one check every 5 minutes
        when = day_start + timedelta(minutes=5 * i)
        run_id = database.start_run(DEMO_DB)
        results = [simulate_check(*host, when, rng) for host in DEMO_HOSTS]
        database.insert_results(run_id, results, DEMO_DB)
        database.finish_run(run_id, "SUCCESS", len(results), sum(r.is_online for r in results), db_path=DEMO_DB)

    for path in report.generate_report(args.date, output_dir=DEMO_OUTPUT, db_path=DEMO_DB):
        print("Report written:", path)


if __name__ == "__main__":
    main()
