"""
config.py - Central settings for the REST API Data Puller.

Change the values in this file to pull weather for a different city,
a different date range, or to change where output files are written.
No other file needs to be edited.
"""

from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Location settings
# ---------------------------------------------------------------------------
# Find coordinates for any city at https://open-meteo.com/en/docs
LOCATION_NAME = "New York, NY"
LATITUDE = 40.7128
LONGITUDE = -74.0060
TIMEZONE = "America/New_York"

# ---------------------------------------------------------------------------
# Date range settings
# ---------------------------------------------------------------------------
# Option 1: set fixed dates as "YYYY-MM-DD" strings, for example:
#   START_DATE = "2025-01-01"
#   END_DATE = "2025-01-31"
# Option 2: leave them as None to pull the most recent DAYS_TO_PULL days.
START_DATE = None
END_DATE = None
DAYS_TO_PULL = 30

# The historical archive is usually a few days behind today,
# so the default end date is shifted back by this many days.
ARCHIVE_DELAY_DAYS = 6

# ---------------------------------------------------------------------------
# API settings
# ---------------------------------------------------------------------------
API_URL = "https://archive-api.open-meteo.com/v1/archive"
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3            # how many times to retry a failed API call
RETRY_WAIT_SECONDS = 2     # wait between retries (doubles each attempt)

# Daily weather fields requested from the API.
# Full list: https://open-meteo.com/en/docs/historical-weather-api
DAILY_VARIABLES = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "precipitation_sum",
    "wind_speed_10m_max",
]

# Units (use "celsius"/"fahrenheit", "mm"/"inch", "kmh"/"mph")
TEMPERATURE_UNIT = "fahrenheit"
PRECIPITATION_UNIT = "inch"
WIND_SPEED_UNIT = "mph"

# ---------------------------------------------------------------------------
# Storage and output settings
# ---------------------------------------------------------------------------
DATABASE_PATH = "data/weather.db"
OUTPUT_DIR = "output"

# Report format: "pdf", "csv", or "both"
REPORT_FORMAT = "both"


def get_date_range():
    """Return (start_date, end_date) as "YYYY-MM-DD" strings.

    Uses the fixed START_DATE/END_DATE if both are set, otherwise
    calculates a rolling window of DAYS_TO_PULL days.
    """
    if START_DATE and END_DATE:
        return START_DATE, END_DATE

    end = date.today() - timedelta(days=ARCHIVE_DELAY_DAYS)
    start = end - timedelta(days=DAYS_TO_PULL - 1)
    return start.isoformat(), end.isoformat()
