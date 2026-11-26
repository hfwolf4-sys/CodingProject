"""
pinger.py - Pings hosts with the operating system's own `ping` command.

Uses subprocess instead of a third-party ping library, so it needs no
extra packages and no admin rights. Works on Windows, macOS, and Linux:
the command-line flags differ per platform, so they are built here, and the
output is parsed with regular expressions to pull out the response times.

The main function is ping_host(), which always returns a PingResult, even
when the host is unreachable or the ping command itself fails.
"""

import platform
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

SYSTEM = platform.system().lower()   # "windows", "darwin" (macOS), or "linux"

# Matches a reply time in ping output, on every platform:
#   Linux/macOS: "time=12.3 ms"     Windows: "time=12ms" or "time<1ms"
REPLY_TIME_RE = re.compile(r"time[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)

# Phrases that mean the hostname could not be resolved to an IP address
DNS_ERROR_PHRASES = (
    "name or service not known",            # Linux
    "temporary failure in name resolution", # Linux, no DNS server reachable
    "cannot resolve",                       # macOS
    "unknown host",                         # macOS / BSD
    "could not find host",                  # Windows
    "no address associated",                # Linux
)

# Phrases that mean a router reported the destination as unreachable
UNREACHABLE_PHRASES = ("unreachable", "network is down", "no route to host")


@dataclass
class PingResult:
    """The outcome of pinging one host once (PING_COUNT echo requests)."""
    host: str
    label: str
    timestamp: str                       # local time, "YYYY-MM-DD HH:MM:SS"
    is_online: bool
    avg_response_ms: Optional[float]     # None when no reply was received
    min_response_ms: Optional[float]
    max_response_ms: Optional[float]
    packets_sent: int
    packets_received: int
    packet_loss_pct: float
    error: Optional[str]                 # why the host is offline, if it is

    def to_dict(self):
        return asdict(self)


def build_ping_command(host, count, timeout_seconds):
    """Return the ping command (as a list) for the current operating system."""
    if SYSTEM == "windows":
        # -n = number of requests, -w = timeout per reply in milliseconds
        return ["ping", "-n", str(count), "-w", str(int(timeout_seconds * 1000)), host]
    if SYSTEM == "darwin":
        # macOS: -c = count, -W = wait per reply in milliseconds
        return ["ping", "-c", str(count), "-W", str(int(timeout_seconds * 1000)), host]
    # Linux: -c = count, -W = wait per reply in seconds (whole number)
    return ["ping", "-c", str(count), "-W", str(max(1, int(timeout_seconds))), host]


def parse_ping_output(output):
    """Return the list of reply times (ms) found in the ping command's output."""
    return [float(t) for t in REPLY_TIME_RE.findall(output)]


def describe_failure(output, return_code):
    """Turn ping's output into a short, readable reason for an offline host."""
    text = output.lower()
    if any(phrase in text for phrase in DNS_ERROR_PHRASES):
        return "DNS lookup failed (host name could not be resolved)"
    if any(phrase in text for phrase in UNREACHABLE_PHRASES):
        return "Destination unreachable"
    if "permission denied" in text or "operation not permitted" in text:
        return "Permission denied (ping not allowed on this system)"
    if return_code not in (0, 1):
        return f"ping command failed (exit code {return_code})"
    return "Request timed out (no reply)"


def ping_host(host, label, count, timeout_seconds):
    """Ping one host and return a PingResult. Never raises: errors become an offline result."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    command = build_ping_command(host, count, timeout_seconds)
    # Give the whole command enough time for every request, plus a margin.
    process_timeout = count * (timeout_seconds + 1) + 5

    def offline(reason):
        return PingResult(host, label, timestamp, False, None, None, None,
                          count, 0, 100.0, reason)

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=process_timeout,
        )
    except FileNotFoundError:
        return offline("ping command not found on this system")
    except subprocess.TimeoutExpired:
        return offline(f"ping did not finish within {process_timeout}s")
    except OSError as exc:
        return offline(f"Could not run ping: {exc}")

    output = (completed.stdout or "") + (completed.stderr or "")
    times = parse_ping_output(output)[:count]

    if not times:
        return offline(describe_failure(output, completed.returncode))

    received = len(times)
    return PingResult(
        host=host,
        label=label,
        timestamp=timestamp,
        is_online=True,
        avg_response_ms=round(sum(times) / received, 3),
        min_response_ms=round(min(times), 3),
        max_response_ms=round(max(times), 3),
        packets_sent=count,
        packets_received=received,
        packet_loss_pct=round((count - received) / count * 100, 1),
        error=None,
    )


def normalize_hosts(host_entries):
    """
    Turn the HOSTS setting into a clean list of (host, label) pairs.

    Accepts plain strings or {"host": ..., "label": ...} dicts, skips blank
    or malformed entries, and removes duplicates while keeping the order.
    """
    hosts, seen = [], set()
    for entry in host_entries:
        if isinstance(entry, str):
            host, label = entry.strip(), entry.strip()
        elif isinstance(entry, dict) and str(entry.get("host", "")).strip():
            host = str(entry["host"]).strip()
            label = str(entry.get("label") or host).strip()
        else:
            continue
        if host and host not in seen:
            seen.add(host)
            hosts.append((host, label))
    return hosts


def ping_all(hosts, count, timeout_seconds, max_parallel=8):
    """
    Ping every (host, label) pair and return a list of PingResult objects,
    in the same order as the input. Hosts are pinged in parallel threads so
    one slow or offline host does not hold up the rest.
    """
    if not hosts:
        return []
    workers = max(1, min(max_parallel, len(hosts)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(ping_host, host, label, count, timeout_seconds)
                   for host, label in hosts]
        return [future.result() for future in futures]
