# Weather App Pro

Hourly weather, a 5-day forecast for any location, and an AI assistant that answers weather questions using real forecast data. The AI works with OpenAI (paid, cheap) or OpenRouter (free models).

## Run it

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then put your OPENAI_API_KEY (or OPENROUTER_API_KEY) in .env
python app.py
```

Open http://127.0.0.1:5000. Without an API key the weather pages still work and the AI panel shows a friendly "not configured" message.

## AI features

| Feature | Endpoint | How it works |
|---|---|---|
| Ask Weather | `POST /api/ask` | Streamed chat. The server fetches the forecast for the place on screen and gives it to the model as source data, so each question costs one AI request. Every answer lists the "Data used". |
| Daily briefing | `POST /api/briefing` | On-demand 2 to 4 sentence summary of the next 24 hours, cached for 30 minutes. |
| Activity planner | `POST /api/plan` | Ranks best and worst time windows for an activity. The model returns JSON that is parsed and validated before display. |

## Installable app (PWA)

The app is a Progressive Web App: it can be installed on a phone or computer and opens in its own window.

- **Android / Chrome / Edge:** open the live site and tap the **Install app** button in the header (or the browser's install option).
- **iPhone / iPad:** open the site in Safari, tap Share, then **Add to Home Screen**.
- Installing requires **HTTPS**. Render provides it automatically; `localhost` also works while developing.

How it works (all in the project, nothing to configure):

| File | Role |
|---|---|
| `static/manifest.webmanifest` | App name, colors, icons and start page (served at `/manifest.webmanifest`) |
| `static/sw.js` | Service worker (served at `/sw.js`): caches the app shell and shows `/offline` when there is no connection |
| `static/pwa.js` | Registers the service worker and powers the Install button |
| `static/icons/` | App icons (192, 512, maskable, Apple touch, favicon) |
| `templates/offline.html` | Page shown when offline |

The service worker never caches weather pages or `/api/*` (AI) calls, so data is always fresh and nothing personal is stored. **When you change `style.css`, `app.js` or `pwa.js`, bump `VERSION` in `static/sw.js`** (for example `v1` to `v2`) so installed apps pick up the new files.

To change the look, edit the color variables at the top of `static/style.css` (a dark mode is included and follows the device setting). To change the icon, replace the PNGs in `static/icons/` keeping the same names and sizes.

## Choosing an AI provider

The app picks the provider from the key it finds in `.env` (OpenAI wins if both are set; force one with `AI_PROVIDER`).

| | OpenAI | OpenRouter |
|---|---|---|
| Key variable | `OPENAI_API_KEY` | `OPENROUTER_API_KEY` |
| Cost | Pay per use (small models cost a fraction of a cent per request) | Free models, about 50 requests a day per account |
| Model setting | `OPENAI_MODEL` (default `gpt-6-luna`) | `OPENROUTER_MODEL` + `OPENROUTER_FALLBACK_MODELS` |
| Best for | Reliable demos and paying customers | Zero-cost experiments |

The OpenAI API is billed separately from a ChatGPT subscription. Add credit and set a monthly usage limit on platform.openai.com before you share the app publicly.

## Protecting your quota or budget

- One AI request per chat question, briefing or plan (no multi-step tool loops).
- An app-wide daily ceiling (`AI_GLOBAL_PER_DAY`) and per-visitor limits (`AI_LIMIT_PER_DAY`), with provider-aware defaults.
- Briefings are on demand and cached for 30 minutes; weather data is cached for 10 minutes.
- Each AI request logs `ai_request` and `ai_response` lines, including which model answered.

**OpenRouter free models:** they rotate and can be busy at peak times. The app uses a specific free instruct model first (`OPENROUTER_MODEL`) and lets OpenRouter fall back to the others in `OPENROUTER_FALLBACK_MODELS` if it is busy or unavailable. The generic `openrouter/free` router is avoided because it can pick models that are not chat models. If you see "model unavailable", choose other free models from https://openrouter.ai/collections/free-models and set them in your environment. Free-model prompts may be logged by the provider, so never send private data.

Each AI request logs which model actually answered (`ai_response ... served_model=...`), which is the first thing to check if answers look wrong.

## Engineering decisions worth knowing

- **Grounding:** the model is told to use only the supplied weather data and never guess numbers.
- **Keys stay on the server:** the browser only talks to your Flask endpoints.
- **Untrusted input:** question length capped, chat history sanitized, activity names cleaned, coordinates range-checked, model output parsed and validated, page text rendered with `textContent`.
- **Graceful failure:** weather and AI errors become short user-facing messages; the normal weather view keeps working.
- **Usage logging:** every AI request writes an `ai_request` log line.

## Files

- `app.py`: routes, validation, rate limits
- `weather.py`: Open-Meteo calls, caching, data shaping
- `ai_assistant.py`: OpenRouter client, prompts, chat, briefing, planner, quota guard
- `utils.py`: TTL cache and usage limiter
- `templates/index.html`, `templates/offline.html`, `static/app.js`, `static/style.css`: interface
- `static/manifest.webmanifest`, `static/sw.js`, `static/pwa.js`, `static/icons/`: PWA files

## Deploy (Render)

1. Push this folder to a GitHub repository (`.env` is git-ignored, so your key is not uploaded).
2. In Render: New > Web Service > connect the repository.
3. Runtime: Python. Build command: `pip install -r requirements.txt`.
   Start command: `gunicorn app:app --workers 1 --threads 8 --timeout 120`
4. Environment tab: add `OPENAI_API_KEY` (or `OPENROUTER_API_KEY`), and optionally `AI_GLOBAL_PER_DAY`.
5. Deploy, open the onrender.com URL, and test a search, the briefing, a chat question and the planner. Then check the install option (see above).

Keep `--workers 1`: the cache and rate limiters live in memory, so extra workers would each keep their own copy.

## Troubleshooting a live site

Open `/status` on your site (for example `https://your-app.onrender.com/status`). It shows whether the server can reach the weather service and whether AI is configured. It never shows secret keys.

- `"http_status": 429` for the weather service means Open-Meteo is limiting this server's IP address. The free service shares limits per IP, and hosting providers share IPs between many apps. Wait and retry, or use a paid or alternative weather provider.
- `"http_status": null` with `Timeout` or `ConnectionError` means the server could not reach the weather service at all. Check Render's status page and logs.
- `"key_configured": false` means the AI key variable is missing in Render's Environment tab.

Render's **Logs** tab shows the same errors (lines starting with "Weather request failed" or "Weather provider returned").

## Before you sell it

- Open-Meteo's free tier is for non-commercial use; a paid product needs a commercial weather plan or another provider.
- Free AI models are fine for a demo but not for paying customers (rate limits, rotation, logging). Use a paid model (OpenAI or OpenRouter), which also makes multi-step tool calling practical again.
- Replace the in-memory cache and limiters with Redis when running more than one server process.
- Add accounts and billing, then tie limits to the user instead of the IP.
