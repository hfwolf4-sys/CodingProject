"""
config.py - Central settings for the Network Uptime Monitor.

Edit the values in this file to change which hosts are checked, how they
are pinged, and where the database and reports are written.
No other file needs to be edited.
"""

# ---------------------------------------------------------------------------
# Hosts to monitor
# ---------------------------------------------------------------------------
# Each entry is a hostname or an IP address. Add as many as you like.
# The last two entries are deliberately unreachable so the report shows
# what an offline host looks like. Remove them for real monitoring.
HOSTS = [
    "127.0.0.1",            # this computer (should always be online)
    "localhost",            # same machine, resolved by name
    "8.8.8.8",              # Google Public DNS
    "1.1.1.1",              # Cloudflare DNS
    "google.com",           # a public website, resolved by DNS
    "10.255.255.1",         # private address that nothing answers (offline example)
    "no-such-host.invalid", # name that cannot be resolved (DNS failure example)
]

# ---------------------------------------------------------------------------
# Ping settings
# ---------------------------------------------------------------------------
PING_COUNT = 3              # echo requests sent to each host per check
PING_TIMEOUT_SECONDS = 2    # how long to wait for each reply
PING_INTERVAL_SECONDS = 0.2 # pause between the echo requests in one check
MAX_WORKERS = 10            # hosts pinged at the same time (threads)

# Which ping method to use:
#   "auto"       - try icmplib first, fall back to the system ping command
#   "icmplib"    - only use the icmplib library
#   "subprocess" - only use the operating system's ping command
PING_METHOD = "auto"

# ---------------------------------------------------------------------------
# Continuous mode
# ---------------------------------------------------------------------------
# Used by: python main.py --continuous
CHECK_INTERVAL_MINUTES = 5

# ---------------------------------------------------------------------------
# Storage and output settings
# ---------------------------------------------------------------------------
DATABASE_PATH = "data/uptime.db"
OUTPUT_DIR = "output"

# Report format: "pdf", "csv", or "both"
REPORT_FORMAT = "both"

# A host whose average response time is above this is flagged as "Slow"
# in the PDF report (it still counts as online).
SLOW_RESPONSE_MS = 200
