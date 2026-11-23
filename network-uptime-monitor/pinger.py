"""
pinger.py - Pings hosts and returns structured results.

Two ways to ping are supported:
    icmplib    - a pure Python library that sends ICMP echo requests itself.
                 Works without admin rights on most Linux/macOS setups
                 ("unprivileged" mode) and with admin/root rights everywhere.
    subprocess - runs the operating system's own `ping` command and reads
                 its output. Works on Windows, macOS, and Linux with no setup.

With PING_METHOD = "auto" (the default), icmplib is tried first and the
system ping command is used if icmplib is not installed or not permitted.

Every check returns a PingResult, whether the host answered or not, so an
unreachable host is recorded as data instead of crashing the program.
"""

import logging
import platform
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional

import config

logger = logging.getLogger(__name__)

try:
    import icmplib
except ImportError:  # icmplib is optional when the system ping command is used
    icmplib = None

# Remembered after the first check so every later ping uses the method that worked.
_icmplib_privileged = None   # True / False once icmplib has been tested
_icmplib_unusable = False    # set if icmplib cannot send pings on this machine


@dataclass
class PingResult:
    """The outcome of one check of one host."""

    host: str
    timestamp: str                      # local time, "YYYY-MM-DD HH:MM:SS"
    is_up: bool                         # True if at least one reply came back
    response_time_ms: Optional[float]   # average round trip in ms (None if offline)
    packets_sent: int
    packets_received: int
    packet_loss: float                  # percent, 0 to 100
    method: str                         # "icmplib" or "subprocess"
    error: Optional[str] = None         # why the host is offline, if known

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------
def ping_host(host):
    """Ping one host and return a PingResult. Never raises for network problems."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    method = config.PING_METHOD.lower()

    if method in ("auto", "icmplib") and icmplib is not None and not _icmplib_unusable:
        result = _ping_with_icmplib(host, timestamp)
        if result is not None:
            return result
        if method == "icmplib":
            return _failed(host, timestamp, "icmplib", "icmplib is not permitted to send pings here")
    elif method == "icmplib":
        return _failed(host, timestamp, "icmplib", "icmplib is not installed (pip install icmplib)")

    return _ping_with_subprocess(host, timestamp)


def ping_all(hosts):
    """Ping every host at the same time using a thread pool.

    Pinging is mostly waiting for replies, so threads let a slow or offline
    host time out without holding up the others. Results come back in the
    same order as `hosts`.
    """
    if not hosts:
        return []
    workers = max(1, min(config.MAX_WORKERS, len(hosts)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_safe_ping, hosts))


# ---------------------------------------------------------------------------
# icmplib method
# ---------------------------------------------------------------------------
def _ping_with_icmplib(host, timestamp):
    """Ping using icmplib. Returns None if icmplib is not allowed on this machine."""
    global _icmplib_privileged, _icmplib_unusable

    # Try unprivileged mode first (no admin rights needed), then privileged mode.
    modes = [_icmplib_privileged] if _icmplib_privileged is not None else [False, True]
    for privileged in modes:
        try:
            reply = icmplib.ping(
                host,
                count=config.PING_COUNT,
                interval=config.PING_INTERVAL_SECONDS,
                timeout=config.PING_TIMEOUT_SECONDS,
                privileged=privileged,
            )
        except icmplib.SocketPermissionError:
            continue  # this mode is not allowed, try the next one
        except icmplib.NameLookupError:
            _icmplib_privileged = privileged
            return _failed(host, timestamp, "icmplib", "Name lookup failed (DNS could not resolve host)")
        except icmplib.ICMPLibError as exc:
            _icmplib_privileged = privileged
            return _failed(host, timestamp, "icmplib", f"Ping error: {exc}")
        except OSError as exc:
            # e.g. "Network is unreachable" when there is no route to the address
            _icmplib_privileged = privileged
            return _failed(host, timestamp, "icmplib", f"Network error: {exc.strerror or exc}")

        _icmplib_privileged = privileged
        is_up = reply.packets_received > 0
        return PingResult(
            host=host,
            timestamp=timestamp,
            is_up=is_up,
            response_time_ms=round(reply.avg_rtt, 3) if is_up else None,
            packets_sent=reply.packets_sent,
            packets_received=reply.packets_received,
            packet_loss=round(reply.packet_loss * 100, 1),
            method="icmplib",
            error=None if is_up else "Request timed out (no reply)",
        )

    # Neither mode worked: use the system ping command from now on.
    _icmplib_unusable = True
    logger.info("icmplib cannot send pings without admin rights here; using the system ping command")
    return None


# ---------------------------------------------------------------------------
# subprocess method (the operating system's ping command)
# ---------------------------------------------------------------------------
def _ping_command(host):
    """Build the ping command for this operating system."""
    count = str(config.PING_COUNT)
    if platform.system().lower() == "windows":
        # -n count, -w timeout in milliseconds
        return ["ping", "-n", count, "-w", str(int(config.PING_TIMEOUT_SECONDS * 1000)), host]
    if platform.system().lower() == "darwin":
        # macOS: -W timeout in milliseconds
        return ["ping", "-c", count, "-W", str(int(config.PING_TIMEOUT_SECONDS * 1000)), host]
    # Linux: -W timeout in whole seconds
    return ["ping", "-c", count, "-W", str(max(1, int(config.PING_TIMEOUT_SECONDS))), host]


def _ping_with_subprocess(host, timestamp):
    """Ping by running the system ping command and parsing its text output."""
    if shutil.which("ping") is None:
        return _failed(host, timestamp, "subprocess",
                       "No ping command found on this system and icmplib is not usable")

    command = _ping_command(host)
    # Allow every echo request its full timeout, plus a few seconds of slack.
    overall_timeout = config.PING_COUNT * (config.PING_TIMEOUT_SECONDS + config.PING_INTERVAL_SECONDS) + 5
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=overall_timeout)
    except subprocess.TimeoutExpired:
        return _failed(host, timestamp, "subprocess", "Ping command timed out")
    except OSError as exc:
        return _failed(host, timestamp, "subprocess", f"Could not run ping: {exc}")

    output = completed.stdout + completed.stderr
    return parse_ping_output(host, timestamp, output)


def parse_ping_output(host, timestamp, output):
    """Turn the text printed by `ping` (Windows, macOS, or Linux) into a PingResult."""
    sent, received = _parse_packet_counts(output)
    times = [float(t) for t in re.findall(r"time[=<]\s*([\d.]+)\s*ms", output, re.IGNORECASE)]
    is_up = received > 0 and bool(times)

    error = None
    if not is_up:
        lowered = output.lower()
        if any(s in lowered for s in ("unknown host", "could not find host", "name or service not known",
                                      "cannot resolve", "temporary failure in name resolution")):
            error = "Name lookup failed (DNS could not resolve host)"
        elif "unreachable" in lowered:
            error = "Destination unreachable"
        else:
            error = "Request timed out (no reply)"

    return PingResult(
        host=host,
        timestamp=timestamp,
        is_up=is_up,
        response_time_ms=round(sum(times) / len(times), 3) if is_up else None,
        packets_sent=sent or config.PING_COUNT,
        packets_received=received if is_up else 0,
        packet_loss=round(100 * (1 - received / sent), 1) if sent and is_up else 100.0,
        method="subprocess",
        error=error,
    )


def _parse_packet_counts(output):
    """Read the sent/received packet counts from ping's summary line."""
    # Linux/macOS: "3 packets transmitted, 3 received" (macOS says "packets received")
    match = re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets\s+)?received", output)
    if match:
        return int(match.group(1)), int(match.group(2))
    # Windows: "Packets: Sent = 3, Received = 3, Lost = 0 (0% loss)"
    match = re.search(r"Sent\s*=\s*(\d+),\s*Received\s*=\s*(\d+)", output)
    if match:
        return int(match.group(1)), int(match.group(2))
    return 0, 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _failed(host, timestamp, method, error):
    """Build a PingResult for a host that could not be reached."""
    return PingResult(
        host=host,
        timestamp=timestamp,
        is_up=False,
        response_time_ms=None,
        packets_sent=config.PING_COUNT,
        packets_received=0,
        packet_loss=100.0,
        method=method,
        error=error,
    )


def _safe_ping(host):
    """Wrapper used by the thread pool: any unexpected error becomes an offline result."""
    try:
        return ping_host(host)
    except Exception as exc:  # noqa: BLE001 - one bad host must not stop the whole cycle
        logger.warning("Unexpected error pinging %s: %s", host, exc)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return _failed(host, timestamp, config.PING_METHOD, f"Unexpected error: {exc}")
