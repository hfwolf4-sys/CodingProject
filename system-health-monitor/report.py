"""
report.py - Builds the daily health report from the database.

Steps:
    1. Load the day's per-host summary (SQL) and individual checks from SQLite
    2. Use pandas to add extra stats: 95th percentile response time, jitter,
       a health rating per host, and an hour-by-hour availability grid
    3. Write two CSV files and one PDF report to OUTPUT_DIR

Any problem writing the files is raised as ReportError.
"""

import os
from datetime import datetime

import pandas as pd
from fpdf import FPDF
from fpdf.enums import XPos, YPos

import config
import database


class ReportError(Exception):
    """Raised when the report data cannot be built or the files cannot be written."""


# Colors (RGB) used in the PDF
GREEN = (46, 160, 67)
AMBER = (219, 151, 0)
RED = (207, 34, 46)
GREY = (215, 218, 222)
DARK = (36, 41, 47)
MUTED = (101, 109, 118)
HEADER_FILL = (240, 243, 246)

HEALTH_COLORS = {"Healthy": GREEN, "Degraded": AMBER, "Critical": RED}


# ---------------------------------------------------------------------------
# Data preparation with pandas
# ---------------------------------------------------------------------------

def rate_health(row):
    """Give a host a one-word health rating for the day, plus the reason."""
    uptime = row["uptime_pct"]
    avg_ms = row["avg_response_ms"]
    loss = row["avg_packet_loss_pct"]

    if uptime < config.CRITICAL_UPTIME_PCT:
        return "Critical", f"Uptime {uptime:.1f}% is below {config.CRITICAL_UPTIME_PCT:g}%"
    problems = []
    if uptime < config.HEALTHY_UPTIME_PCT:
        problems.append(f"uptime {uptime:.1f}%")
    if pd.notna(avg_ms) and avg_ms > config.SLOW_RESPONSE_MS:
        problems.append(f"slow ({avg_ms:.0f} ms avg)")
    if loss > config.MAX_PACKET_LOSS_PCT:
        problems.append(f"packet loss {loss:.1f}%")
    if not row["current_online"]:
        problems.append("offline at last check")
    if problems:
        text = "; ".join(problems)
        return "Degraded", text[0].upper() + text[1:]
    return "Healthy", "Within all limits"


def build_report_data(day, db_path=None):
    """
    Load and summarize one day of results.

    Returns (summary_df, checks_df, hourly_df), or None if there is no data.
    """
    summary_rows = database.get_daily_summary(day, db_path)
    check_rows = database.get_results_for_day(day, db_path)
    if not summary_rows:
        return None

    summary = pd.DataFrame(summary_rows)
    checks = pd.DataFrame(check_rows)
    checks["timestamp"] = pd.to_datetime(checks["timestamp"])

    # Extra response-time stats from the individual checks (online checks only)
    online = checks[checks["is_online"] == 1]
    latency_stats = online.groupby("host")["avg_response_ms"].agg(
        p95_response_ms=lambda s: s.quantile(0.95),
        jitter_ms="std",
    ).round(2)
    summary = summary.merge(latency_stats, how="left", left_on="host", right_index=True)

    summary["status"] = summary["current_online"].map({1: "Online", 0: "Offline"})
    ratings = summary.apply(rate_health, axis=1, result_type="expand")
    summary["health"], summary["health_reason"] = ratings[0], ratings[1]

    # Hour-by-hour uptime %: one row per host, one column per hour (0-23)
    checks["hour"] = checks["timestamp"].dt.hour
    hourly = (checks.pivot_table(index="host", columns="hour", values="is_online", aggfunc="mean") * 100)
    hourly = hourly.reindex(index=summary["host"], columns=range(24))

    return summary, checks, hourly


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

CSV_COLUMNS = {
    "host": "Host",
    "label": "Label",
    "status": "Status",
    "health": "Health",
    "health_reason": "Health Notes",
    "uptime_pct": "Uptime (%)",
    "avg_response_ms": "Avg Response (ms)",
    "min_response_ms": "Min Response (ms)",
    "max_response_ms": "Max Response (ms)",
    "p95_response_ms": "P95 Response (ms)",
    "jitter_ms": "Jitter (ms)",
    "avg_packet_loss_pct": "Avg Packet Loss (%)",
    "checks": "Checks",
    "online_checks": "Online Checks",
    "first_check": "First Check",
    "last_check": "Last Check",
    "last_error": "Last Error",
}


def write_csv(summary, checks, day, output_dir):
    """Write the summary and the raw checks as CSV files. Returns the file paths."""
    summary_path = os.path.join(output_dir, f"health_summary_{day}.csv")
    checks_path = os.path.join(output_dir, f"health_checks_{day}.csv")

    summary[list(CSV_COLUMNS)].rename(columns=CSV_COLUMNS).to_csv(summary_path, index=False)

    raw = checks.drop(columns=["hour"], errors="ignore").copy()
    raw["status"] = raw["is_online"].map({1: "Online", 0: "Offline"})
    raw = raw[["timestamp", "host", "label", "status", "avg_response_ms",
               "packets_sent", "packets_received", "packet_loss_pct", "error"]]
    raw.to_csv(checks_path, index=False, date_format="%Y-%m-%d %H:%M:%S")

    return [summary_path, checks_path]


# ---------------------------------------------------------------------------
# PDF output
# ---------------------------------------------------------------------------

def fmt_ms(value):
    return "-" if pd.isna(value) else f"{value:,.2f}"


class HealthReportPDF(FPDF):
    """A4 landscape PDF with a shared footer on every page."""

    def __init__(self, day):
        super().__init__(orientation="L", unit="mm", format="A4")
        self.day = day
        self.set_auto_page_break(auto=True, margin=15)
        self.set_margins(12, 12, 12)

    def footer(self):
        self.set_y(-10)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 5, f"System Health Monitor  |  {self.day}  |  Page {self.page_no()}/{{nb}}", align="C")

    # Small helpers so the layout code below stays readable
    def heading(self, text):
        # Start a new page rather than leave a heading alone at the bottom
        if self.will_page_break(25):
            self.add_page()
        else:
            self.ln(4)
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(*DARK)
        self.cell(0, 8, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def table_header(self, columns):
        self.set_font("Helvetica", "B", 8.5)
        self.set_fill_color(*HEADER_FILL)
        self.set_text_color(*DARK)
        for title, width, align in columns:
            self.cell(width, 7, title, border="B", align=align, fill=True)
        self.ln()


def draw_overview(pdf, summary, checks):
    """Row of headline numbers across the top of the report."""
    online_now = int(summary["current_online"].sum())
    total_checks = len(checks)
    overall_uptime = checks["is_online"].mean() * 100
    avg_ms = checks.loc[checks["is_online"] == 1, "avg_response_ms"].mean()
    counts = summary["health"].value_counts()

    tiles = [
        ("Hosts monitored", f"{len(summary)}", DARK),
        ("Online now", f"{online_now} / {len(summary)}", GREEN if online_now == len(summary) else AMBER),
        ("Overall uptime", f"{overall_uptime:.2f}%", DARK),
        ("Avg response", f"{fmt_ms(avg_ms)} ms", DARK),
        ("Checks run", f"{total_checks:,}", DARK),
        ("Healthy / Degraded / Critical",
         f"{counts.get('Healthy', 0)} / {counts.get('Degraded', 0)} / {counts.get('Critical', 0)}", DARK),
    ]
    width = (pdf.w - pdf.l_margin - pdf.r_margin) / len(tiles)
    y = pdf.get_y()
    for i, (title, value, color) in enumerate(tiles):
        x = pdf.l_margin + i * width
        pdf.set_draw_color(*GREY)
        pdf.rect(x + 1, y, width - 2, 18)
        pdf.set_xy(x + 1, y + 2)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*MUTED)
        pdf.cell(width - 2, 4, title, align="C")
        pdf.set_xy(x + 1, y + 7)
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*color)
        pdf.cell(width - 2, 9, value, align="C")
    pdf.set_y(y + 21)


def draw_host_table(pdf, summary):
    """One row per host: status, health, uptime, and response-time stats."""
    columns = [
        ("Host", 58, "L"), ("Address", 40, "L"), ("Status", 20, "C"), ("Health", 22, "C"),
        ("Uptime", 22, "R"), ("Avg ms", 22, "R"), ("P95 ms", 22, "R"), ("Max ms", 22, "R"),
        ("Loss", 18, "R"), ("Checks", 27, "R"),
    ]
    pdf.table_header(columns)
    pdf.set_draw_color(*GREY)

    for _, row in summary.iterrows():
        values = [
            (row["label"], DARK, ""),
            (row["host"], MUTED, ""),
            (row["status"], GREEN if row["status"] == "Online" else RED, "B"),
            (row["health"], HEALTH_COLORS[row["health"]], "B"),
            (f"{row['uptime_pct']:.2f}%", DARK, ""),
            (fmt_ms(row["avg_response_ms"]),
             AMBER if pd.notna(row["avg_response_ms"]) and row["avg_response_ms"] > config.SLOW_RESPONSE_MS else DARK, ""),
            (fmt_ms(row["p95_response_ms"]), DARK, ""),
            (fmt_ms(row["max_response_ms"]), DARK, ""),
            (f"{row['avg_packet_loss_pct']:.1f}%", DARK, ""),
            (f"{int(row['online_checks'])}/{int(row['checks'])}", DARK, ""),
        ]
        for (text, color, style), (_, width, align) in zip(values, columns):
            pdf.set_font("Helvetica", style, 8.5)
            pdf.set_text_color(*color)
            pdf.cell(width, 6.5, str(text), border="B", align=align)
        pdf.ln()


def draw_hourly_grid(pdf, summary, hourly):
    """Grid of hosts x 24 hours, each cell colored by that hour's uptime."""
    name_width = 58
    cell_width = (pdf.w - pdf.l_margin - pdf.r_margin - name_width) / 24
    cell_height = 5.5

    # Hour labels
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*MUTED)
    pdf.cell(name_width, 5, "")
    for hour in range(24):
        pdf.cell(cell_width, 5, f"{hour:02d}", align="C")
    pdf.ln()

    labels = dict(zip(summary["host"], summary["label"]))
    for host, hours in hourly.iterrows():
        if pdf.will_page_break(cell_height + 1):
            pdf.add_page()
        y = pdf.get_y()
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*DARK)
        pdf.cell(name_width, cell_height, labels.get(host, host))
        for hour in range(24):
            value = hours[hour]
            if pd.isna(value):
                color = (246, 248, 250)      # no checks in this hour
            elif value >= config.HEALTHY_UPTIME_PCT:
                color = GREEN
            elif value >= config.CRITICAL_UPTIME_PCT or value > 0:
                color = AMBER
            else:
                color = RED
            pdf.set_fill_color(*color)
            pdf.rect(pdf.l_margin + name_width + hour * cell_width + 0.4, y + 0.6,
                     cell_width - 0.8, cell_height - 1.2, style="F")
        pdf.set_y(y + cell_height)

    # Legend (kept together on one page)
    pdf.ln(2)
    if pdf.will_page_break(6):
        pdf.add_page()
    pdf.set_font("Helvetica", "", 7.5)
    for color, text in [(GREEN, "All checks online"), (AMBER, "Some checks failed"),
                        (RED, "All checks failed"), ((246, 248, 250), "No checks")]:
        pdf.set_fill_color(*color)
        pdf.set_draw_color(*GREY)
        pdf.rect(pdf.get_x(), pdf.get_y() + 1, 3, 3, style="DF")
        pdf.set_x(pdf.get_x() + 4)
        pdf.set_text_color(*MUTED)
        pdf.cell(38, 5, text)
    pdf.ln(6)


def draw_issues(pdf, summary):
    """List every host that is not Healthy, with the reason and last error."""
    issues = summary[summary["health"] != "Healthy"]
    pdf.set_font("Helvetica", "", 9)
    if issues.empty:
        pdf.set_text_color(*GREEN)
        pdf.cell(0, 6, "No issues: every host met the health rules for the day.",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        return
    for _, row in issues.iterrows():
        pdf.set_text_color(*HEALTH_COLORS[row["health"]])
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(22, 6, row["health"])
        pdf.set_text_color(*DARK)
        pdf.cell(70, 6, f"{row['label']} ({row['host']})")
        pdf.set_font("Helvetica", "", 9)
        detail = row["health_reason"]
        if pd.notna(row["last_error"]) and row["last_error"]:
            detail += f". Last error: {row['last_error']}"
        pdf.multi_cell(0, 6, detail, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def write_pdf(summary, checks, hourly, day, output_dir):
    """Build the PDF report and return its path."""
    path = os.path.join(output_dir, f"health_report_{day}.pdf")
    pdf = HealthReportPDF(day)
    pdf.add_page()

    # Title block
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*DARK)
    pdf.cell(0, 10, "System Health Report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*MUTED)
    first, last = checks["timestamp"].min(), checks["timestamp"].max()
    pdf.cell(0, 6,
             f"Day: {day}    Checks from {first:%H:%M:%S} to {last:%H:%M:%S}    "
             f"Generated {datetime.now():%Y-%m-%d %H:%M:%S}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)

    draw_overview(pdf, summary, checks)
    pdf.heading("Host Summary")
    draw_host_table(pdf, summary)
    pdf.heading("Hourly Availability")
    draw_hourly_grid(pdf, summary, hourly)
    pdf.heading("Issues")
    draw_issues(pdf, summary)

    # Explain the health rules so the report stands on its own
    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(
        0, 4.5,
        f"Status is the result of each host's most recent check. Health rules: Healthy = uptime of at least "
        f"{config.HEALTHY_UPTIME_PCT:g}%, average response time of at most {config.SLOW_RESPONSE_MS:g} ms, "
        f"packet loss of at most {config.MAX_PACKET_LOSS_PCT:g}%, and online at the last check. "
        f"Critical = uptime below {config.CRITICAL_UPTIME_PCT:g}%. Anything in between is Degraded. "
        f"P95 = 95% of checks were at least this fast.",
    )

    pdf.output(path)
    return path


# ---------------------------------------------------------------------------
# Entry point used by main.py
# ---------------------------------------------------------------------------

def generate_report(day, output_dir=None, db_path=None):
    """
    Build the CSV and PDF reports for one day ('YYYY-MM-DD').
    Returns the list of files written, or an empty list if there is no data.
    Raises database.DatabaseError or ReportError on failure.
    """
    output_dir = output_dir or config.OUTPUT_DIR
    data = build_report_data(day, db_path)
    if data is None:
        return []
    summary, checks, hourly = data

    try:
        os.makedirs(output_dir, exist_ok=True)
        files = write_csv(summary, checks, day, output_dir)
        files.append(write_pdf(summary, checks, hourly, day, output_dir))
    except PermissionError as exc:
        raise ReportError(f"Cannot write {exc.filename}: is the file open in another program?") from exc
    except OSError as exc:
        raise ReportError(f"Could not write the report files: {exc}") from exc
    except Exception as exc:   # pandas or fpdf2 problems with unexpected data
        raise ReportError(f"Report generation failed: {exc}") from exc
    return files
