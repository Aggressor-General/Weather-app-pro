"""Weather data layer (Open-Meteo): geocoding, forecast, and helpers.

Everything the AI assistant knows about the weather comes through this module,
so the model never has to guess numbers.
"""
import logging
from datetime import datetime, timedelta

import requests

from utils import TTLCache

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT = 10  # seconds

# Wind unit sent to Open-Meteo: "kmh", "kn" (knots), "mph" or "ms".
WIND_UNIT = "kmh"
WIND_LABELS = {"kmh": "km/h", "kn": "kn", "mph": "mph", "ms": "m/s"}

_geo_cache = TTLCache(ttl_seconds=24 * 3600)
_weather_cache = TTLCache(ttl_seconds=10 * 60)


class WeatherError(Exception):
    """The weather provider could not be reached or returned unusable data."""


def _get_json(url, params):
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Weather provider request failed: %s", exc)
        raise WeatherError(
            "The weather service is not responding right now. Please try again in a moment."
        ) from exc


def get_coordinates(city_name):
    city_name = (city_name or "").strip()
    if not city_name:
        return None

    key = city_name.lower()
    cached = _geo_cache.get(key)
    if cached:
        return cached

    data = _get_json(GEOCODE_URL, {"name": city_name, "count": 1})
    results = data.get("results")
    if not results:
        return None  # city not found

    result = results[0]
    location = {
        "name": result.get("name", city_name),
        "country": result.get("country", ""),
        "lat": result["latitude"],
        "lon": result["longitude"],
    }
    _geo_cache.set(key, location)
    return location


def get_weather(lat, lon):
    key = (round(float(lat), 2), round(float(lon), 2))
    cached = _weather_cache.get(key)
    if cached is not None:
        return cached

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                   "wind_speed_10m,wind_direction_10m,weather_code",
        "hourly": "weather_code,precipitation_probability,precipitation,"
                  "temperature_2m,relative_humidity_2m,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code,"
                 "precipitation_probability_max,precipitation_sum,wind_speed_10m_max",
        "timezone": "auto",  # times are local to the searched place
        "wind_speed_unit": WIND_UNIT,
        "forecast_days": 5,
    }
    data = _get_json(FORECAST_URL, params)
    if not all(k in data for k in ("current", "hourly", "daily")):
        raise WeatherError("The weather service returned incomplete data.")

    _weather_cache.set(key, data)
    return data


WEATHER_CODES = {
    0: ("Clear sky", "☀️"),
    1: ("Mainly clear", "🌤️"),
    2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"),
    45: ("Fog", "🌫️"),
    48: ("Depositing rime fog", "🌫️"),
    51: ("Light drizzle", "🌦️"),
    53: ("Moderate drizzle", "🌦️"),
    55: ("Dense drizzle", "🌧️"),
    56: ("Light freezing drizzle", "🌧️"),
    57: ("Dense freezing drizzle", "🌧️"),
    61: ("Slight rain", "🌦️"),
    63: ("Moderate rain", "🌧️"),
    65: ("Heavy rain", "🌧️"),
    66: ("Light freezing rain", "🌧️"),
    67: ("Heavy freezing rain", "🌧️"),
    71: ("Slight snow", "🌨️"),
    73: ("Moderate snow", "🌨️"),
    75: ("Heavy snow", "❄️"),
    77: ("Snow grains", "🌨️"),
    80: ("Slight rain showers", "🌦️"),
    81: ("Moderate rain showers", "🌧️"),
    82: ("Violent rain showers", "⛈️"),
    85: ("Slight snow showers", "🌨️"),
    86: ("Heavy snow showers", "❄️"),
    95: ("Thunderstorm", "⛈️"),
    96: ("Thunderstorm with hail", "⛈️"),
    99: ("Severe thunderstorm with hail", "⛈️"),
}


def degrees_to_compass(degrees):
    directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    index = round(degrees / 22.5) % 16
    return directions[index]


def describe_weather(code):
    return WEATHER_CODES.get(code, ("Unknown", "❓"))


def get_forecast_windows(hourly):
    times = hourly["time"]
    codes = hourly["weather_code"]
    rain_chance = hourly["precipitation_probability"]

    windows = []
    current_window = None

    for i in range(len(times)):
        desc, icon = describe_weather(codes[i])
        rain = rain_chance[i] or 0

        # Start a new window, or extend the current one if the weather matches
        if current_window is None or current_window["desc"] != desc:
            if current_window is not None:
                windows.append(current_window)
            current_window = {
                "desc": desc,
                "icon": icon,
                "start": times[i],
                "end": times[i],
                "max_rain": rain,
            }
        else:
            current_window["end"] = times[i]
            current_window["max_rain"] = max(current_window["max_rain"], rain)

    if current_window is not None:
        windows.append(current_window)

    return windows


# ---------------------------------------------------------------------------
# Helpers that shape the raw API response for the page and for the AI
# ---------------------------------------------------------------------------

def _parse_time(value):
    return datetime.fromisoformat(value)


def current_hour_index(raw):
    """Index in the hourly arrays that matches the current local hour."""
    now = _parse_time(raw["current"]["time"]).replace(minute=0, second=0, microsecond=0)
    try:
        return raw["hourly"]["time"].index(now.strftime("%Y-%m-%dT%H:%M"))
    except ValueError:
        return 0


def upcoming_hourly(raw):
    """The hourly block trimmed to start at the current hour."""
    start = current_hour_index(raw)
    return {key: values[start:] for key, values in raw["hourly"].items()}


def hourly_rows(raw, start=0, hours=24):
    h = raw["hourly"]
    rows = []
    for i in range(max(0, start), min(len(h["time"]), start + hours)):
        desc, icon = describe_weather(h["weather_code"][i])
        moment = _parse_time(h["time"][i])
        rows.append({
            "stamp": moment.strftime("%a %d %b %H:%M"),
            "hour": moment.strftime("%H:%M"),
            "temp": h["temperature_2m"][i],
            "rain_chance": h["precipitation_probability"][i],
            "rain_mm": h["precipitation"][i],
            "wind": h["wind_speed_10m"][i],
            "humidity": h["relative_humidity_2m"][i],
            "desc": desc,
            "icon": icon,
        })
    return rows


def daily_rows(raw, days=5):
    d = raw["daily"]
    rows = []
    for i in range(min(days, len(d["time"]))):
        desc, icon = describe_weather(d["weather_code"][i])
        rows.append({
            "date": d["time"][i],
            "label": _parse_time(d["time"][i]).strftime("%a %d %b"),
            "desc": desc,
            "icon": icon,
            "min": d["temperature_2m_min"][i],
            "max": d["temperature_2m_max"][i],
            "rain": d["precipitation_probability_max"][i],
            "rain_mm": d["precipitation_sum"][i],
            "wind_max": d["wind_speed_10m_max"][i],
        })
    return rows


def format_window_range(start, end):
    """'Thu 15 Oct, 15:00-18:00' (end is shown as the end of the last hour)."""
    first = _parse_time(start)
    last = _parse_time(end) + timedelta(hours=1)
    if first.date() == last.date() or (last.hour == 0 and last.minute == 0
                                        and (last - first) <= timedelta(hours=24)):
        return f"{first.strftime('%a %d %b, %H:%M')}-{last.strftime('%H:%M')}"
    return f"{first.strftime('%a %H:%M')} to {last.strftime('%a %H:%M')}"


def units():
    return {"temperature": "°C", "wind": WIND_LABELS.get(WIND_UNIT, WIND_UNIT), "rain": "mm"}


def compact(rows, drop=("icon", "time", "hour")):
    """Remove display-only fields so tool results stay small (fewer tokens)."""
    return [{k: v for k, v in row.items() if k not in drop} for row in rows]


def build_snapshot(location, raw):
    """Current conditions + 5-day forecast in a compact, AI-friendly shape."""
    current = raw["current"]
    desc, _ = describe_weather(current["weather_code"])
    return {
        "location": {
            "name": location["name"],
            "country": location.get("country", ""),
            "lat": location["lat"],
            "lon": location["lon"],
        },
        "timezone": raw.get("timezone"),
        "local_time_now": _parse_time(current["time"]).strftime("%A %d %B %Y, %H:%M"),
        "units": units(),
        "current": {
            "temp": current["temperature_2m"],
            "feels_like": current.get("apparent_temperature"),
            "humidity": current["relative_humidity_2m"],
            "wind": current["wind_speed_10m"],
            "wind_direction": degrees_to_compass(current["wind_direction_10m"]),
            "conditions": desc,
        },
        "daily": compact(daily_rows(raw, 5), drop=("icon", "date")),
    }


def hourly_as_text(rows):
    """One line per hour: the cheapest way to hand 120 hours of data to a model."""
    u = units()
    lines = []
    for r in rows:
        lines.append(
            f"{r['stamp']} | {r['temp']}{u['temperature']} | rain {r['rain_chance']}% "
            f"({r['rain_mm']} mm) | wind {r['wind']} {u['wind']} | "
            f"humidity {r['humidity']}% | {r['desc']}"
        )
    return "\n".join(lines)


def daily_as_text(rows):
    """One line per day for the model."""
    u = units()
    return "\n".join(
        f"{r['label']} | {r['desc']} | {r['min']}-{r['max']}{u['temperature']} | "
        f"rain chance {r['rain']}% ({r['rain_mm']} mm) | max wind {r['wind_max']} {u['wind']}"
        for r in rows
    )
