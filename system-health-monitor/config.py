"""
config.py - Every setting for the System Health Monitor in one place.

Edit this file to change which hosts are checked, how they are pinged,
how "healthy" is defined, and where the database and reports are written.
No other file needs to be edited.
"""

# ---------------------------------------------------------------------------
# Hosts to monitor
# ---------------------------------------------------------------------------
# Each entry is either a plain hostname / IP address string, or a dict with
# a "host" and a friendly "label" that is shown in the reports.
# The last two entries are deliberately unreachable so the report shows what
# an offline host looks like. Remove them when monitoring real systems.
HOSTS = [
    {"host": "127.0.0.1", "label": "Local machine"},
    {"host": "localhost", "label": "Local machine (by name)"},
    {"host": "8.8.8.8", "label": "Google Public DNS"},
    {"host": "1.1.1.1", "label": "Cloudflare DNS"},
    {"host": "github.com", "label": "GitHub"},
    {"host": "10.255.255.1", "label": "Offline example"},
    {"host": "server.invalid", "label": "DNS failure example"},
]

# ---------------------------------------------------------------------------
# Ping settings
# ---------------------------------------------------------------------------
PING_COUNT = 4              # echo requests sent to each host per check
PING_TIMEOUT_SECONDS = 2    # how long to wait for each reply
MAX_PARALLEL_PINGS = 8      # hosts pinged at the same time

# ---------------------------------------------------------------------------
# Continuous mode (python main.py --continuous)
# ---------------------------------------------------------------------------
CHECK_INTERVAL_MINUTES = 5

# ---------------------------------------------------------------------------
# Health rules used in the report
# ---------------------------------------------------------------------------
# A host is rated for the day as:
#   Healthy  - uptime >= HEALTHY_UPTIME_PCT, latency and packet loss within limits
#   Degraded - online most of the day but slow, lossy, or with short outages
#   Critical - uptime below CRITICAL_UPTIME_PCT
HEALTHY_UPTIME_PCT = 99.0
CRITICAL_UPTIME_PCT = 90.0
SLOW_RESPONSE_MS = 150.0    # average response time above this is "slow"
MAX_PACKET_LOSS_PCT = 5.0   # average packet loss above this is "lossy"

# ---------------------------------------------------------------------------
# Storage and output
# ---------------------------------------------------------------------------
DATABASE_PATH = "data/health.db"
OUTPUT_DIR = "output"
LOG_FILE = "logs/monitor.log"   # set to None to log to the console only
