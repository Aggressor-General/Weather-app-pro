import json
import logging
import os

from dotenv import load_dotenv

load_dotenv()  # must run before the modules below read environment variables

from flask import (Flask, Response, jsonify, render_template, request, send_from_directory,
                   stream_with_context)
from werkzeug.middleware.proxy_fix import ProxyFix

import ai_assistant as ai
import weather as wx
from utils import UsageLimiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

# On hosts like Render, requests arrive through a proxy. Trust one proxy hop so
# request.remote_addr is the real visitor (otherwise everyone shares one rate limit).
if os.getenv("TRUST_PROXY") == "1" or os.getenv("RENDER"):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

# Per-visitor limits. The app-wide daily ceiling is AI_GLOBAL_PER_DAY (see ai_assistant.py),
# so each visitor gets a small share.
chat_limiter = UsageLimiter(
    per_minute=int(os.getenv("AI_LIMIT_PER_MINUTE", ai.DEFAULT_VISITOR_PER_MINUTE)),
    per_day=int(os.getenv("AI_LIMIT_PER_DAY", ai.DEFAULT_VISITOR_PER_DAY)),
)
briefing_limiter = UsageLimiter(per_minute=3, per_day=8)

# Only show windows for notable weather (skip plain clear/cloudy stretches)
SKIP_DESCRIPTIONS = {"Clear sky", "Mainly clear", "Partly cloudy", "Overcast"}


@app.route("/", methods=["GET", "POST"])
def home():
    weather_data = None
    error = None

    # The search form uses GET (/?city=Lagos) so refresh, back and shared links all work,
    # which matters in an installed app. POST is still accepted for older forms.
    city = (request.values.get("city") or "").strip()

    if city:
        try:
            location = wx.get_coordinates(city)

            if location is None:
                error = f"Couldn't find a city called '{city}'. Try again?"
            else:
                raw = wx.get_weather(location["lat"], location["lon"])
                current = raw["current"]

                desc, icon = wx.describe_weather(current["weather_code"])
                wind_dir = wx.degrees_to_compass(current["wind_direction_10m"])

                all_windows = wx.get_forecast_windows(wx.upcoming_hourly(raw))
                notable_windows = [w for w in all_windows if w["desc"] not in SKIP_DESCRIPTIONS]
                for w in notable_windows:
                    w["range"] = wx.format_window_range(w["start"], w["end"])

                weather_data = {
                    "city": location["name"],
                    "country": location["country"],
                    "lat": location["lat"],
                    "lon": location["lon"],
                    "temp": current["temperature_2m"],
                    "feels_like": current.get("apparent_temperature"),
                    "humidity": current["relative_humidity_2m"],
                    "wind": current["wind_speed_10m"],
                    "wind_unit": wx.units()["wind"],
                    "wind_dir": wind_dir,
                    "desc": desc,
                    "icon": icon,
                    "hourly": wx.hourly_rows(raw, wx.current_hour_index(raw), 24),
                    "forecast": wx.daily_rows(raw, 5),
                    "windows": notable_windows[:8],  # cap it so the page doesn't get huge
                }
        except wx.WeatherError as exc:
            error = str(exc)

    return render_template("index.html", weather=weather_data, error=error)


# ---------------------------------------------------------------------------
# Progressive Web App files
#
# The manifest and service worker are served from the site root (not /static/) so the
# service worker can control the whole app.
# ---------------------------------------------------------------------------

@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(app.static_folder, "manifest.webmanifest",
                               mimetype="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    response = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache"      # browsers must always check for a new worker
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.get("/offline")
def offline():
    return render_template("offline.html")


# ---------------------------------------------------------------------------
# AI endpoints
# ---------------------------------------------------------------------------

def parse_location(payload):
    """Validate the location the browser sends; never trust client input."""
    loc = (payload or {}).get("location")
    if not isinstance(loc, dict):
        return None
    try:
        lat, lon = float(loc.get("lat")), float(loc.get("lon"))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {
        "name": str(loc.get("name") or "this location")[:80],
        "country": str(loc.get("country") or "")[:60],
        "lat": lat,
        "lon": lon,
    }


def json_error(message, status):
    return jsonify({"error": message}), status


def friendly_error(exc):
    """Map internal exceptions to (message, HTTP status)."""
    if isinstance(exc, ai.AIUnavailable):
        return str(exc), 503
    if isinstance(exc, (ai.AIError, wx.WeatherError)):
        return str(exc), 502
    if isinstance(exc, ValueError):
        return str(exc), 400
    logger.exception("Unexpected error in AI endpoint")
    return "Something went wrong. Please try again.", 500


@app.post("/api/briefing")
def api_briefing():
    payload = request.get_json(silent=True) or {}
    location = parse_location(payload)
    if location is None:
        return json_error("Search for a location first.", 400)
    if not briefing_limiter.allow(request.remote_addr):
        return json_error("Too many requests. Please slow down.", 429)

    try:
        return jsonify({"briefing": ai.generate_briefing(location)})
    except Exception as exc:
        message, status = friendly_error(exc)
        return json_error(message, status)


@app.post("/api/plan")
def api_plan():
    payload = request.get_json(silent=True) or {}
    location = parse_location(payload)
    if location is None:
        return json_error("Search for a location first.", 400)
    if not chat_limiter.allow(request.remote_addr):
        return json_error("You have reached the AI usage limit. Please try again later.", 429)

    try:
        return jsonify(ai.plan_activity(payload.get("activity"), location))
    except Exception as exc:
        message, status = friendly_error(exc)
        return json_error(message, status)


@app.post("/api/ask")
def api_ask():
    payload = request.get_json(silent=True) or {}
    location = parse_location(payload)
    if location is None:
        return json_error("Search for a location first.", 400)

    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        return json_error("Type a question first.", 400)
    question = question.strip()
    if len(question) > ai.MAX_QUESTION_CHARS:
        return json_error(f"Please keep questions under {ai.MAX_QUESTION_CHARS} characters.", 400)

    if not chat_limiter.allow(request.remote_addr):
        return json_error("You have reached the AI usage limit. Please try again later.", 429)

    history = payload.get("history")
    if not isinstance(history, list):
        history = []

    def event_stream():
        try:
            for event in ai.stream_chat(question, history, location):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            message, _ = friendly_error(exc)
            yield f"data: {json.dumps({'type': 'error', 'message': message})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1")
