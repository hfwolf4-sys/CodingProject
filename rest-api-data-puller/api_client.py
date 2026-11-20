"""
api_client.py - Calls the Open-Meteo weather API and parses the JSON response.

Open-Meteo is free and needs no API key: https://open-meteo.com
"""

import logging
import time

import requests

import config

logger = logging.getLogger(__name__)


class APIError(Exception):
    """Raised when weather data cannot be retrieved or parsed."""


def fetch_weather(start_date, end_date):
    """Request daily weather data for the configured location.

    Retries on network errors and server errors (5xx). Client errors
    such as a bad date (4xx) are not retried because they will not fix
    themselves.

    Returns the parsed JSON response as a Python dictionary.
    """
    params = {
        "latitude": config.LATITUDE,
        "longitude": config.LONGITUDE,
        "start_date": start_date,
        "end_date": end_date,
        "daily": ",".join(config.DAILY_VARIABLES),
        "timezone": config.TIMEZONE,
        "temperature_unit": config.TEMPERATURE_UNIT,
        "precipitation_unit": config.PRECIPITATION_UNIT,
        "wind_speed_unit": config.WIND_SPEED_UNIT,
    }

    wait = config.RETRY_WAIT_SECONDS
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            logger.info("Calling Open-Meteo API (attempt %d of %d)", attempt, config.MAX_RETRIES)
            response = requests.get(
                config.API_URL, params=params, timeout=config.REQUEST_TIMEOUT_SECONDS
            )

            # 4xx means our request was wrong; report the API's own message and stop.
            if 400 <= response.status_code < 500:
                reason = _error_reason(response)
                raise APIError(f"API rejected the request ({response.status_code}): {reason}")

            response.raise_for_status()  # raises for 5xx server errors
            return response.json()

        except APIError:
            raise
        except (requests.exceptions.RequestException, ValueError) as exc:
            # Network problem, timeout, server error, or invalid JSON
            problem = _describe_failure(exc)
            logger.warning("API call failed: %s", problem)
            if attempt == config.MAX_RETRIES:
                raise APIError(
                    f"Gave up after {config.MAX_RETRIES} attempts. Last error: {problem}"
                ) from exc
            time.sleep(wait)
            wait *= 2  # back off a little more each time


def parse_daily_records(payload):
    """Turn the API's column-style JSON into a list of row dictionaries.

    The API returns data like:
        {"daily": {"time": ["2025-01-01", ...], "temperature_2m_max": [41.2, ...]}}
    This function reshapes it into:
        [{"date": "2025-01-01", "temperature_2m_max": 41.2, ...}, ...]
    """
    daily = payload.get("daily")
    if not daily or "time" not in daily:
        raise APIError("API response is missing the 'daily' data section.")

    dates = daily["time"]
    records = []
    for i, day in enumerate(dates):
        row = {"date": day}
        for variable in config.DAILY_VARIABLES:
            values = daily.get(variable, [])
            # Some days can be missing a value; store None instead of failing.
            row[variable] = values[i] if i < len(values) else None
        records.append(row)

    logger.info("Parsed %d daily records from the API response", len(records))
    return records


def _describe_failure(exc):
    """Give a short, readable description of a failed request."""
    if isinstance(exc, requests.exceptions.Timeout):
        return f"request timed out after {config.REQUEST_TIMEOUT_SECONDS} seconds"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "could not connect (check your internet connection, proxy, or firewall)"
    if isinstance(exc, requests.exceptions.HTTPError):
        return f"server error {exc.response.status_code}"
    if isinstance(exc, ValueError):
        return "response was not valid JSON"
    return str(exc)


def _error_reason(response):
    """Pull the human-readable error message out of an API error response."""
    try:
        return response.json().get("reason", response.text)
    except ValueError:
        return response.text
