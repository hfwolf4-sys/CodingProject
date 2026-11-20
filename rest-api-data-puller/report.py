"""
report.py - Builds a summary report from the stored weather data.

Outputs (controlled by config.REPORT_FORMAT):
    CSV - <name>_summary.csv (key statistics) and <name>_daily.csv (every day)
    PDF - <name>_report.pdf (statistics, notable days, weekly summary, daily table)
"""

import logging
import os
from datetime import datetime

import pandas as pd
from fpdf import FPDF

import config

logger = logging.getLogger(__name__)

# Readable labels for API field names. Units are filled in from config.py.
UNIT_SYMBOLS = {"fahrenheit": "°F", "celsius": "°C"}


def _labels():
    """Map each API variable name to a readable column label with units."""
    temp = UNIT_SYMBOLS.get(config.TEMPERATURE_UNIT, config.TEMPERATURE_UNIT)
    return {
        "temperature_2m_max": f"High Temp ({temp})",
        "temperature_2m_min": f"Low Temp ({temp})",
        "temperature_2m_mean": f"Avg Temp ({temp})",
        "precipitation_sum": f"Precipitation ({config.PRECIPITATION_UNIT})",
        "wind_speed_10m_max": f"Max Wind ({config.WIND_SPEED_UNIT})",
    }


def label(variable):
    """Return the readable label for a variable, or a tidy version of its name."""
    return _labels().get(variable, variable.replace("_", " ").title())


def build_summary(df):
    """Calculate summary statistics with pandas. Returns a DataFrame."""
    rows = []
    for variable in config.DAILY_VARIABLES:
        series = df[variable].dropna()
        if series.empty:
            continue
        rows.append(
            {
                "Metric": label(variable),
                "Average": round(series.mean(), 2),
                "Minimum": round(series.min(), 2),
                "Maximum": round(series.max(), 2),
                "Total": round(series.sum(), 2) if variable == "precipitation_sum" else None,
                "Days With Data": int(series.count()),
            }
        )
    return pd.DataFrame(rows)


def build_highlights(df):
    """Pick out notable days (hottest, coldest, wettest, windiest)."""
    highlights = []

    def add(title, variable, pick_max=True):
        if variable not in df or df[variable].dropna().empty:
            return
        idx = df[variable].idxmax() if pick_max else df[variable].idxmin()
        day = df.loc[idx, "date"].strftime("%a %b %d, %Y")
        highlights.append(f"{title}: {day} ({df.loc[idx, variable]} {_unit_of(variable)})")

    add("Hottest day", "temperature_2m_max")
    add("Coldest night", "temperature_2m_min", pick_max=False)
    add("Wettest day", "precipitation_sum")
    add("Windiest day", "wind_speed_10m_max")

    if "precipitation_sum" in df:
        rainy = int((df["precipitation_sum"] > 0).sum())
        highlights.append(f"Days with precipitation: {rainy} of {len(df)}")
    return highlights


def build_weekly(df):
    """Summarize each calendar week (Monday to Sunday).

    Precipitation is totaled for the week; every other measure is averaged.
    """
    rules = {v: ("sum" if v == "precipitation_sum" else "mean") for v in config.DAILY_VARIABLES}
    weekly = (
        df.set_index("date")[config.DAILY_VARIABLES]
        .resample("W-SUN")
        .agg(rules)
        .round(2)
        .reset_index()
    )
    weekly["date"] = (weekly["date"] - pd.Timedelta(days=6)).dt.strftime("Week of %b %d")
    names = _labels()
    if "precipitation_sum" in names:
        names["precipitation_sum"] = "Total " + names["precipitation_sum"]
    return weekly.rename(columns={"date": "Week", **names})


def generate_reports(df, start_date, end_date):
    """Write the report file(s) requested in config. Returns a list of file paths."""
    if df.empty:
        raise ValueError("No weather data found in the database for this date range.")

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    base_name = f"weather_{_slug(config.LOCATION_NAME)}_{start_date}_to_{end_date}"
    fmt = config.REPORT_FORMAT.lower()
    if fmt not in ("pdf", "csv", "both"):
        raise ValueError(f"REPORT_FORMAT must be 'pdf', 'csv', or 'both', not {fmt!r}")

    summary = build_summary(df)
    created = []
    if fmt in ("csv", "both"):
        created += write_csv(df, summary, base_name)
    if fmt in ("pdf", "both"):
        created.append(write_pdf(df, summary, base_name, start_date, end_date))

    for path in created:
        logger.info("Report written: %s", path)
    return created


def write_csv(df, summary, base_name):
    """Write the summary statistics and the full daily data to two CSV files."""
    summary_path = os.path.join(config.OUTPUT_DIR, f"{base_name}_summary.csv")
    daily_path = os.path.join(config.OUTPUT_DIR, f"{base_name}_daily.csv")

    summary.to_csv(summary_path, index=False)

    daily = df[["date"] + config.DAILY_VARIABLES].copy()
    daily["date"] = daily["date"].dt.strftime("%Y-%m-%d")
    daily.rename(columns={"date": "Date", **_labels()}).to_csv(daily_path, index=False)

    return [summary_path, daily_path]


def write_pdf(df, summary, base_name, start_date, end_date):
    """Write a formatted PDF report."""
    path = os.path.join(config.OUTPUT_DIR, f"{base_name}_report.pdf")

    pdf = FPDF(orientation="P", unit="mm", format="Letter")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Title block
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "Weather Summary Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, f"Location: {config.LOCATION_NAME} ({config.LATITUDE}, {config.LONGITUDE})",
             new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Period: {start_date} to {end_date} ({len(df)} days)",
             new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Generated: {datetime.now():%Y-%m-%d %H:%M}  |  Source: Open-Meteo API",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Key statistics
    _section_heading(pdf, "Key Statistics")
    _table(pdf, summary.fillna("-"))

    # Notable days
    _section_heading(pdf, "Highlights")
    pdf.set_font("Helvetica", "", 10)
    for line in build_highlights(df):
        pdf.cell(0, 6, f"- {line}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Weekly summary
    _section_heading(pdf, "Weekly Summary")
    _table(pdf, build_weekly(df))

    # Full daily detail
    _section_heading(pdf, "Daily Detail")
    daily = df[["date"] + config.DAILY_VARIABLES].copy()
    daily["date"] = daily["date"].dt.strftime("%Y-%m-%d")
    _table(pdf, daily.rename(columns={"date": "Date", **_labels()}).fillna("-"))

    pdf.output(path)
    return path


def _section_heading(pdf, text):
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")


def _table(pdf, frame):
    """Draw a pandas DataFrame as a simple PDF table."""
    pdf.set_font("Helvetica", "", 8)
    with pdf.table(text_align="CENTER", line_height=5) as table:
        header = table.row()
        for column in frame.columns:
            header.cell(str(column))
        for values in frame.itertuples(index=False):
            row = table.row()
            for value in values:
                row.cell(str(value))
    pdf.ln(4)


def _unit_of(variable):
    """Extract the unit text from a label, e.g. 'High Temp (°F)' -> '°F'."""
    text = label(variable)
    return text[text.find("(") + 1 : text.rfind(")")] if "(" in text else ""


def _slug(text):
    """Make text safe for a file name, e.g. 'New York, NY' -> 'new_york_ny'."""
    cleaned = "".join(c if c.isalnum() else "_" for c in text.lower())
    return "_".join(part for part in cleaned.split("_") if part)
