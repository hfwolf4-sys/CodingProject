"""
report.py - Builds the daily uptime summary from the stored ping results.

Outputs (controlled by config.REPORT_FORMAT), one set per day:
    CSV - uptime_summary_<day>.csv (one row per host) and uptime_checks_<day>.csv (every check)
    PDF - uptime_report_<day>.pdf (overview, host table, uptime chart, outage list)

Running the report again for the same day overwrites that day's files with
up-to-date numbers, which is what continuous mode does after every cycle.
"""

import logging
import os
from datetime import datetime

import pandas as pd
from fpdf import FPDF

import config

logger = logging.getLogger(__name__)

# Colors used in the PDF (red, green, blue values)
GREEN = (46, 125, 50)
RED = (198, 40, 40)
ORANGE = (230, 140, 0)
DARK = (33, 37, 41)
GRAY = (108, 117, 125)
LIGHT_ROW = (245, 247, 250)
HEADER_BG = (33, 64, 104)


class ReportError(Exception):
    """Raised when there is nothing to report or a report file cannot be written."""


# ---------------------------------------------------------------------------
# Summary calculations (pandas)
# ---------------------------------------------------------------------------
def build_host_summary(df):
    """Return one row per host with status, uptime %, and response time stats.

    Status is based on the most recent check of the day: Online if that check
    got a reply, Offline if it did not. Uptime % is the share of all checks
    that day that got a reply. Response times only use successful checks.
    """
    df = df.sort_values("timestamp")
    grouped = df.groupby("host", sort=False)

    summary = pd.DataFrame({
        "Checks": grouped.size(),
        "Successful": grouped["is_up"].sum().astype(int),
        "Avg Response (ms)": grouped["response_time_ms"].mean(),   # mean() skips NULLs
        "Min Response (ms)": grouped["response_time_ms"].min(),
        "Max Response (ms)": grouped["response_time_ms"].max(),
        "Avg Packet Loss (%)": grouped["packet_loss"].mean(),
        "First Check": grouped["timestamp"].min(),
        "Last Check": grouped["timestamp"].max(),
        "Last Up": grouped["is_up"].last(),
        "Last Error": grouped["error"].last(),
    })
    summary["Failed"] = summary["Checks"] - summary["Successful"]
    summary["Uptime (%)"] = summary["Successful"] / summary["Checks"] * 100
    summary["Status"] = summary["Last Up"].map({True: "Online", False: "Offline"})
    summary.loc[summary["Last Up"], "Last Error"] = None   # only show errors for offline hosts

    # Keep hosts in the order they appear in config.py, then any others alphabetically.
    order = {host: i for i, host in enumerate(config.HOSTS)}
    summary = summary.reset_index()
    summary["_order"] = summary["host"].map(lambda h: order.get(h, len(order)))
    summary = summary.sort_values(["_order", "host"]).drop(columns=["_order", "Last Up"])
    summary = summary.rename(columns={"host": "Host"})

    for col in ["Avg Response (ms)", "Min Response (ms)", "Max Response (ms)"]:
        summary[col] = summary[col].round(2)
    summary["Uptime (%)"] = summary["Uptime (%)"].round(2)
    summary["Avg Packet Loss (%)"] = summary["Avg Packet Loss (%)"].round(1)

    columns = ["Host", "Status", "Uptime (%)", "Avg Response (ms)", "Min Response (ms)",
               "Max Response (ms)", "Avg Packet Loss (%)", "Checks", "Successful", "Failed",
               "First Check", "Last Check", "Last Error"]
    return summary[columns].reset_index(drop=True)


def build_overview(df, summary):
    """Headline numbers for the top of the PDF."""
    total_checks = len(df)
    up_checks = int(df["is_up"].sum())
    return {
        "Hosts monitored": len(summary),
        "Online now": int((summary["Status"] == "Online").sum()),
        "Offline now": int((summary["Status"] == "Offline").sum()),
        "Checks recorded": total_checks,
        "Overall uptime": f"{up_checks / total_checks * 100:.2f}%" if total_checks else "n/a",
        "Avg response (online)": (
            f"{df['response_time_ms'].mean():.2f} ms" if df["response_time_ms"].notna().any() else "n/a"
        ),
    }


def find_outages(df):
    """Group back-to-back failed checks for each host into outage periods.

    Example: if a host fails at 10:00, 10:05 and 10:10 and is back at 10:15,
    that is one outage from 10:00 to 10:10 with 3 failed checks.
    """
    outages = []
    for host, checks in df.sort_values("timestamp").groupby("host", sort=False):
        # A new "block" starts every time is_up changes value.
        block_id = (checks["is_up"] != checks["is_up"].shift()).cumsum()
        for _, block in checks[~checks["is_up"]].groupby(block_id[~checks["is_up"]]):
            outages.append({
                "Host": host,
                "Start": block["timestamp"].min(),
                "End": block["timestamp"].max(),
                "Failed Checks": len(block),
                "Ongoing": not checks.loc[checks.index > block.index.max(), "is_up"].any(),
                "Error": block["error"].dropna().iloc[-1] if block["error"].notna().any() else "No reply",
            })
    columns = ["Host", "Start", "End", "Failed Checks", "Ongoing", "Error"]
    return pd.DataFrame(outages, columns=columns).sort_values("Start", ignore_index=True)


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------
def write_csv_reports(df, summary, day):
    """Write the per-host summary and the raw checks for the day."""
    summary_path = os.path.join(config.OUTPUT_DIR, f"uptime_summary_{day}.csv")
    checks_path = os.path.join(config.OUTPUT_DIR, f"uptime_checks_{day}.csv")

    # Write times as full "YYYY-MM-DD HH:MM:SS" text so spreadsheets read them consistently.
    out = summary.copy()
    for col in ("First Check", "Last Check"):
        out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M:%S")
    out.to_csv(summary_path, index=False)

    checks = df.copy()
    checks["timestamp"] = checks["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
    checks["status"] = checks["is_up"].map({True: "Online", False: "Offline"})
    checks = checks[["timestamp", "host", "status", "response_time_ms", "packets_sent",
                     "packets_received", "packet_loss", "method", "error"]]
    checks.to_csv(checks_path, index=False)
    return [summary_path, checks_path]


# ---------------------------------------------------------------------------
# PDF output (fpdf2)
# ---------------------------------------------------------------------------
class UptimePDF(FPDF):
    """FPDF with a running header and page numbers in the footer."""

    def __init__(self, day):
        super().__init__(orientation="P", unit="mm", format="Letter")
        self.day = day
        self.set_auto_page_break(auto=True, margin=15)
        self.set_margins(15, 15, 15)

    def header(self):
        if self.page_no() == 1:
            return  # the first page has its own large title
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 6, f"Network Uptime Report - {self.day}", align="R", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 6, f"Page {self.page_no()}/{{nb}}", align="C")

    def section_title(self, text):
        if self.get_y() > self.h - 45:
            self.add_page()
        self.ln(4)
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(*HEADER_BG)
        self.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*HEADER_BG)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)
        self.set_text_color(*DARK)

    def table_header(self, headers, widths):
        self.set_font("Helvetica", "B", 8.5)
        self.set_fill_color(*HEADER_BG)
        self.set_text_color(255, 255, 255)
        for text, width in zip(headers, widths):
            self.cell(width, 7, text, border=0, align="C", fill=True)
        self.ln()
        self.set_text_color(*DARK)


def _fmt(value, suffix="", digits=2):
    """Format a number for the PDF, showing a dash for missing values."""
    if value is None or pd.isna(value):
        return "-"
    return f"{value:.{digits}f}{suffix}"


def _short(text, max_len):
    """Shorten long text so it fits in a table cell."""
    text = str(text)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def _latin1(text):
    """The built-in PDF fonts only support Latin-1, so replace anything else."""
    return str(text).encode("latin-1", "replace").decode("latin-1")


def write_pdf_report(df, summary, outages, overview, day):
    """Build the PDF report and return its path."""
    pdf = UptimePDF(day)
    pdf.alias_nb_pages()
    pdf.add_page()

    # --- Title block -------------------------------------------------------
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*HEADER_BG)
    pdf.cell(0, 10, "Network Uptime Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*GRAY)
    day_label = datetime.strptime(day, "%Y-%m-%d").strftime("%A, %B %d, %Y")
    first, last = df["timestamp"].min(), df["timestamp"].max()
    pdf.cell(0, 5, f"Day: {day_label}   |   Checks from {first:%H:%M} to {last:%H:%M}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, f"Generated {datetime.now():%Y-%m-%d %H:%M:%S}", new_x="LMARGIN", new_y="NEXT")

    # --- Overview tiles ----------------------------------------------------
    pdf.section_title("Overview")
    tile_w = (pdf.w - pdf.l_margin - pdf.r_margin - 5 * 3) / 6
    x0, y0 = pdf.l_margin, pdf.get_y()
    for i, (name, value) in enumerate(overview.items()):
        x = x0 + i * (tile_w + 3)
        pdf.set_fill_color(*LIGHT_ROW)
        pdf.rect(x, y0, tile_w, 18, style="F")
        color = RED if name == "Offline now" and value else GREEN if name == "Online now" else DARK
        pdf.set_text_color(*color)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_xy(x, y0 + 2)
        pdf.cell(tile_w, 8, str(value), align="C")
        pdf.set_text_color(*GRAY)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_xy(x, y0 + 10)
        pdf.cell(tile_w, 6, name, align="C")
    pdf.set_xy(x0, y0 + 22)
    pdf.set_text_color(*DARK)

    # --- Host summary table ------------------------------------------------
    pdf.section_title("Host Summary")
    headers = ["Host", "Status", "Uptime", "Avg ms", "Min ms", "Max ms", "Loss", "Checks", "Last Check"]
    widths = [45, 18, 18, 17, 16, 16, 14, 15, 26.9]
    pdf.table_header(headers, widths)
    pdf.set_font("Helvetica", "", 8.5)
    for i, row in summary.iterrows():
        fill = i % 2 == 0
        pdf.set_fill_color(*(LIGHT_ROW if fill else (255, 255, 255)))
        pdf.cell(widths[0], 7, _latin1(_short(row["Host"], 26)), fill=fill)
        pdf.set_text_color(*(GREEN if row["Status"] == "Online" else RED))
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.cell(widths[1], 7, row["Status"], align="C", fill=fill)
        pdf.set_font("Helvetica", "", 8.5)
        uptime = row["Uptime (%)"]
        pdf.set_text_color(*(GREEN if uptime >= 99 else ORANGE if uptime >= 90 else RED))
        pdf.cell(widths[2], 7, _fmt(uptime, "%"), align="C", fill=fill)
        avg = row["Avg Response (ms)"]
        slow = pd.notna(avg) and avg > config.SLOW_RESPONSE_MS
        pdf.set_text_color(*(ORANGE if slow else DARK))
        pdf.cell(widths[3], 7, _fmt(avg), align="C", fill=fill)
        pdf.set_text_color(*DARK)
        pdf.cell(widths[4], 7, _fmt(row["Min Response (ms)"]), align="C", fill=fill)
        pdf.cell(widths[5], 7, _fmt(row["Max Response (ms)"]), align="C", fill=fill)
        pdf.cell(widths[6], 7, _fmt(row["Avg Packet Loss (%)"], "%", 0), align="C", fill=fill)
        pdf.cell(widths[7], 7, f"{row['Successful']}/{row['Checks']}", align="C", fill=fill)
        pdf.cell(widths[8], 7, f"{row['Last Check']:%H:%M:%S}", align="C", fill=fill)
        pdf.ln()
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(*GRAY)
    pdf.multi_cell(0, 4, _latin1(
        "Status = result of the most recent check. Uptime = successful checks / all checks for the day. "
        "Response times are averages of successful pings only. "
        f"Orange response times are above {config.SLOW_RESPONSE_MS} ms."
    ), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*DARK)

    # --- Uptime bar chart --------------------------------------------------
    pdf.section_title("Uptime by Host")
    label_w, bar_max = 50, pdf.w - pdf.l_margin - pdf.r_margin - 50 - 20
    for _, row in summary.iterrows():
        if pdf.get_y() > pdf.h - 25:
            pdf.add_page()
        y = pdf.get_y()
        pdf.set_font("Helvetica", "", 8.5)
        pdf.cell(label_w, 6, _latin1(_short(row["Host"], 28)))
        pdf.set_fill_color(230, 233, 237)
        pdf.rect(pdf.l_margin + label_w, y + 1, bar_max, 4, style="F")
        uptime = row["Uptime (%)"]
        if uptime > 0:
            pdf.set_fill_color(*(GREEN if uptime >= 99 else ORANGE if uptime >= 90 else RED))
            pdf.rect(pdf.l_margin + label_w, y + 1, bar_max * uptime / 100, 4, style="F")
        pdf.set_x(pdf.l_margin + label_w + bar_max + 2)
        pdf.cell(18, 6, _fmt(uptime, "%", 1), new_x="LMARGIN", new_y="NEXT")

    # --- Outages -----------------------------------------------------------
    pdf.section_title("Outages")
    if outages.empty:
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 6, "No failed checks recorded for this day.", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.set_font("Helvetica", "", 8.5)
        pdf.cell(0, 5, "Back-to-back failed checks for a host are grouped into one outage.",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
        headers = ["Host", "From", "To", "Failed", "Reason"]
        widths = [42, 20, 20, 15, 88.9]
        pdf.table_header(headers, widths)
        for i, row in outages.iterrows():
            if pdf.get_y() > pdf.h - 22:
                pdf.add_page()
                pdf.table_header(headers, widths)
            fill = i % 2 == 0
            pdf.set_fill_color(*(LIGHT_ROW if fill else (255, 255, 255)))
            pdf.set_font("Helvetica", "", 8.5)
            pdf.cell(widths[0], 7, _latin1(_short(row["Host"], 24)), fill=fill)
            pdf.cell(widths[1], 7, f"{row['Start']:%H:%M:%S}", align="C", fill=fill)
            end = "ongoing" if row["Ongoing"] else f"{row['End']:%H:%M:%S}"
            pdf.cell(widths[2], 7, end, align="C", fill=fill)
            pdf.cell(widths[3], 7, str(row["Failed Checks"]), align="C", fill=fill)
            pdf.cell(widths[4], 7, _latin1(_short(row["Error"], 55)), fill=fill)
            pdf.ln()

    path = os.path.join(config.OUTPUT_DIR, f"uptime_report_{day}.pdf")
    pdf.output(path)
    return path


# ---------------------------------------------------------------------------
# Entry point used by main.py
# ---------------------------------------------------------------------------
def generate_reports(df, day):
    """Create the CSV and/or PDF reports for one day. Returns the list of files written."""
    if df.empty:
        raise ReportError(f"No ping results found for {day}. Run a check first (python main.py).")

    fmt = config.REPORT_FORMAT.lower()
    if fmt not in ("pdf", "csv", "both"):
        raise ReportError(f"REPORT_FORMAT must be 'pdf', 'csv', or 'both', not {config.REPORT_FORMAT!r}")

    try:
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    except OSError as exc:
        raise ReportError(f"Could not create output folder {config.OUTPUT_DIR}: {exc}") from exc

    summary = build_host_summary(df)
    files = []

    try:
        if fmt in ("csv", "both"):
            files += write_csv_reports(df, summary, day)
        if fmt in ("pdf", "both"):
            outages = find_outages(df)
            overview = build_overview(df, summary)
            files.append(write_pdf_report(df, summary, outages, overview, day))
    except PermissionError as exc:
        raise ReportError(f"Could not write report (is the file open in another program?): {exc}") from exc
    except OSError as exc:
        raise ReportError(f"Could not write report: {exc}") from exc

    for path in files:
        logger.info("Report written: %s", path)
    return files
