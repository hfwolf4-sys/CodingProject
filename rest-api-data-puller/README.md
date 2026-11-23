# REST API Data Puller

A small Python data pipeline that pulls daily weather data from the free
[Open-Meteo](https://open-meteo.com) REST API, stores it in a local SQLite
database, and produces a summary report as a PDF and/or CSV files.

It shows the full flow of a typical integration or reporting job:
**call an API → parse JSON → load into a database → query → report**,
with logging, retries, and error handling at each step.

## Features

- Calls the Open-Meteo Historical Weather API (no API key or sign-up needed)
- Parses the JSON response into clean, row-based records
- Stores data in SQLite with no duplicate rows: re-running a date range updates existing rows
- Keeps an audit table (`pull_log`) of every run, including failures and error messages
- Uses pandas to calculate averages, minimums, maximums, totals, and weekly summaries
- Generates a formatted PDF report and/or CSV exports
- Retries failed API calls with increasing wait times and logs clear error messages
- All settings (location, dates, units, output) live in one file: `config.py`

## Requirements

- Python 3.9 or newer
- An internet connection (to reach `archive-api.open-meteo.com`)
- Python packages listed in `requirements.txt`:
  - `requests` for the API calls
  - `pandas` for data handling
  - `fpdf2` for PDF generation
  - `sqlite3` is built into Python, so nothing extra is needed

## Installation

```bash
git clone https://github.com/hfwolf4-sys/CodingProject.git
cd CodingProjects/rest-api-data-puller

# Optional but recommended: create a virtual environment
python -m venv venv
source venv/bin/activate        # On Windows: venv\Scripts\activate

pip install -r requirements.txt
```

## How to Run

Run the full pipeline (pull, store, report) with one command:

```bash
python main.py
```

Optional flags:

| Command | What it does |
|---|---|
| `python main.py` | Pulls data from the API, saves it, and builds the report |
| `python main.py --report-only` | Skips the API call and builds a report from data already in the database |
| `python main.py --format csv` | Builds only CSV files for this run (`pdf`, `csv`, or `both`) |

The script exits with code `0` on success and `1` on failure, so it can be
scheduled with Windows Task Scheduler or cron and monitored.

## Configuration

Edit `config.py` to change what the pipeline pulls. Nothing is hardcoded in the other files.

| Setting | Purpose | Default |
|---|---|---|
| `LOCATION_NAME`, `LATITUDE`, `LONGITUDE`, `TIMEZONE` | Where to pull weather for | New York, NY |
| `START_DATE`, `END_DATE` | Fixed date range (`"YYYY-MM-DD"`) | `None` (use rolling window) |
| `DAYS_TO_PULL` | Size of the rolling window when no fixed dates are set | 30 days |
| `DAILY_VARIABLES` | Which weather measures to request | High, low, and mean temp, precipitation, max wind |
| `TEMPERATURE_UNIT`, `PRECIPITATION_UNIT`, `WIND_SPEED_UNIT` | Units | °F, inches, mph |
| `MAX_RETRIES`, `REQUEST_TIMEOUT_SECONDS` | API error handling | 3 retries, 30 seconds |
| `DATABASE_PATH` | SQLite file location | `data/weather.db` |
| `OUTPUT_DIR`, `REPORT_FORMAT` | Where reports go and which format | `output/`, `both` |

The historical archive runs a few days behind today, so the rolling window ends
`ARCHIVE_DELAY_DAYS` (6) days ago.

## Project Structure

```
rest-api-data-puller/
├── main.py           # Entry point: runs the pipeline steps in order and handles failures
├── config.py         # All settings: location, date range, units, file paths, report format
├── api_client.py     # Calls the Open-Meteo API, retries on failure, parses the JSON
├── database.py       # Creates SQLite tables, saves rows (upsert), logs runs, queries data
├── report.py         # Uses pandas to summarize data and writes the PDF and CSV reports
├── requirements.txt  # Python package dependencies
└── README.md
```

Created when the script runs (not committed to Git):

```
data/weather.db       # SQLite database
output/               # Generated reports
```

### Database tables

**`weather_daily`**: one row per location per day

| Column | Description |
|---|---|
| `location`, `latitude`, `longitude` | Where the data is for |
| `date` | The day (`YYYY-MM-DD`) |
| one column per item in `DAILY_VARIABLES` | e.g. `temperature_2m_max`, `precipitation_sum` |
| `pulled_at` | When the row was last loaded from the API |

**`pull_log`**: one row per pipeline run, with the date range, rows saved,
`SUCCESS`/`FAILED` status, and the error message for failed runs.

You can inspect the data with any SQLite tool, for example:

```bash
sqlite3 data/weather.db "SELECT date, temperature_2m_max, precipitation_sum FROM weather_daily ORDER BY date DESC LIMIT 5;"
sqlite3 data/weather.db "SELECT * FROM pull_log ORDER BY id DESC LIMIT 5;"
```

## Example Output

Console output from a successful run:

```
14:49:38  INFO     Location: New York, NY | Dates: 2026-10-16 to 2026-11-14
14:49:38  INFO     Database ready at data/weather.db
14:49:38  INFO     Calling Open-Meteo API (attempt 1 of 3)
14:49:38  INFO     Parsed 30 daily records from the API response
14:49:38  INFO     Saved 30 rows to weather_daily
14:49:38  INFO     Report written: output/weather_new_york_ny_2026-10-16_to_2026-11-14_summary.csv
14:49:38  INFO     Report written: output/weather_new_york_ny_2026-10-16_to_2026-11-14_daily.csv
14:49:38  INFO     Report written: output/weather_new_york_ny_2026-10-16_to_2026-11-14_report.pdf
14:49:38  INFO     Done. 30 rows in report. Files created:
```

Files written to `output/`:

- **`..._report.pdf`**: a one to two page report with
  - a header showing the location, date range, and when it was generated
  - **Key Statistics**: average, minimum, and maximum for each measure, plus total precipitation
  - **Highlights**: hottest day, coldest night, wettest day, windiest day, and number of days with precipitation
  - **Weekly Summary**: weekly averages, with total precipitation per week
  - **Daily Detail**: a table of every day in the range
- **`..._summary.csv`**: the Key Statistics table, ready to open in Excel
- **`..._daily.csv`**: every day's values with readable column headers

Example **Weekly Summary** table from a real PDF report (New York, Oct 16 to Nov 14, 2026):

| Week | High Temp (°F) | Low Temp (°F) | Avg Temp (°F) | Total Precipitation (inch) | Max Wind (mph) |
|---|---|---|---|---|---|
| Week of Oct 12 | 63.87 | 45.93 | 54.53 | 0.31 | 9.73 |
| Week of Oct 19 | 63.7 | 43.49 | 52.61 | 0.0 | 7.89 |
| Week of Oct 26 | 65.6 | 45.51 | 55.3 | 0.02 | 7.33 |
| Week of Nov 02 | 67.57 | 51.17 | 59.13 | 0.95 | 8.59 |
| Week of Nov 09 | 55.6 | 42.9 | 48.68 | 0.96 | 10.8 |


## Error Handling

| Problem | What happens |
|---|---|
| No internet, timeout, or API server error (5xx) | Retries up to `MAX_RETRIES` times with increasing waits, then stops with a clear message |
| Bad request (4xx), e.g. an invalid date | Stops right away and shows the API's own error message |
| Unexpected or incomplete JSON | Stops with a message; missing single values are stored as empty (NULL) |
| Database can't be opened or written | Stops with a message; writes are rolled back so no partial data is saved |
| No data stored for the date range | Report step explains that no data was found |

Every API run, successful or failed, is recorded in the `pull_log` table.

## Possible Extensions

- Pull several locations in one run from a list in `config.py`
- Schedule a daily run with Task Scheduler or cron
- Add charts to the PDF with matplotlib
- Swap SQLite for SQL Server or PostgreSQL

## Data Source

Weather data from [Open-Meteo](https://open-meteo.com), free for non-commercial use
under the CC BY 4.0 license.
