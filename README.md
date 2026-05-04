# Creator Sourcing Agent

Python CLI that accepts a brand or niche query, discovers public creator profiles through Google search (SerpAPI), enriches them with YouTube Data API + Apify actors (TikTok/Instagram), scores each creator with a transparent 100-point rubric, and writes ranked JSON/CSV outputs.

## What it does

```bash
cd /path/to/taurus    # repo root (folder with main.py)
python main.py "dog wellness creators"
```

You can add **soft numeric floors in the same string** (parsed by regex, then applied after scoring):

- **Avg likes:** e.g. `avg likes at least 5k`, `min average likes 2000`, `over 10k avg likes`
- **Avg comments:** e.g. `avg comments at least 50`, `min average comments 100`
- **Engagement rate:** same scale as the UI (`0–1` internally): e.g. `engagement rate over 5%`, `over 6% engagement`, `min engagement 0.04` (meaning 4%)

If you ask for a minimum and a creator is missing that metric, they are filtered out.

Writes:

- `outputs/creators.json` — pretty-printed JSON with optional `score_breakdown`
- `outputs/creators.csv` — flat columns for spreadsheets
- `outputs/errors.log` — recoverable failures (API keys missing, HTTP errors, Actor failures)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` with live keys (see below).

Configuration lives in `config/settings.yaml` (timeouts, Apify actor IDs, **recall** search budgets, **rerank** weights, and **global calibration** for the display `fit_score`).

## Required API keys

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | Query generation, summaries, LLM scoring facets, fit reasons |
| `SERPAPI_KEY` | Google search via SerpAPI |
| `YOUTUBE_API_KEY` | Channel/video metadata for YouTube creators (enable **YouTube Data API v3** in Google Cloud; for server/CLI use, avoid API-key restrictions that block the v3 endpoints, or you may see **403** and missing likes/comments) |
| `APIFY_API_TOKEN` | Run TikTok/Instagram Apify Actors |

## How to run (by yourself)

1. **Open a terminal** (Terminal.app on macOS, or your IDE’s integrated terminal).

2. **Go to the project folder** (the directory that contains `main.py` and `.env`):

   ```bash
   cd /path/to/taurus
   ```

   Replace `/path/to/taurus` with wherever you saved the repo (for example `~/Downloads/taurus`).

3. **First time only — Python environment and dependencies:**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   cp .env.example .env               # skip if you already have .env
   ```

   Edit `.env` and paste your four API keys (one per line, no quotes needed).

4. **Every time you want a run:**

   ```bash
   cd /path/to/taurus
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   python main.py "your search here"
   ```

   Example:

   ```bash
   python main.py "pet supplements Instagram creators under 50k followers"
   ```

5. **What you should see:** log lines like `Enriched @handle (TikTok)` (or Instagram/YouTube), then a summary of the **top 10** creators, and paths to:

   - `outputs/creators.json`
   - `outputs/creators.csv`

   `outputs/errors.log` is **cleared at the start of each run** and only fills if something failed that run (empty usually means all integrations worked).

6. **If something fails:** check `outputs/errors.log`, confirm `.env` is in **this same folder** as `main.py`, and that you have internet access and API billing/quotas enabled for each provider.

## Web UI (browser)

One-time UI build (requires [Node.js](https://nodejs.org/) 18+):

```bash
cd ui
npm install
npm run build
```

**Option A — single server (recommended):** API + static UI on port 8000.

```bash
cd /path/to/taurus
source .venv/bin/activate
uvicorn server:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000** , enter your query, wait for the run to finish (often a few minutes). Results show the same fields as `outputs/creators.json` (cards or table) with **Download JSON** / **Download CSV**.

**Option B — hot reload while editing the UI:** two terminals.

```bash
# Terminal 1 (from repo root)
source .venv/bin/activate && uvicorn server:app --reload --host 127.0.0.1 --port 8000

# Terminal 2
cd ui && npm run dev
```

Open **http://127.0.0.1:5173** (Vite proxies `/api` to the backend on 8000).

## Data sources & enrichment

See `docs/DATA_SOURCES.md` for provider-level notes. TikTok/Instagram paths call configurable Apify actors and map flexible JSON into the internal `CreatorCandidate` model.

## Scoring

See `docs/SCORING.md` for weights and mechanics.

## Tradeoffs & scaling

- `docs/TRADEOFFS.md`
- `docs/SCALING.md`

## Project layout

Matches the Cursor spec: `src/` modules, `prompts/` for OpenAI templates, `outputs/` for artifacts, `server.py` + `ui/` for the web app.

## If you moved the project folder (TLS / certifi errors)

A Python venv stores **absolute paths** inside `pyvenv.cfg`, `bin/activate`, and every
`bin/*` script shebang (e.g. `uvicorn`). After a move, you may see:

`OSError: Could not find a suitable TLS CA certificate bundle, invalid path: .../old/path/.../certifi/cacert.pem`

**Fix:** from the repo root, either **recreate** the venv (`rm -rf .venv && python3 -m venv .venv && pip install -r requirements.txt`), or rewrite the old path to the new one inside `.venv` and open a **fresh terminal** (in case `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` was set to the old `cacert.pem`).

**“Failed to fetch” on Run search (UI) while the terminal looks fine:** often the browser lost the connection before the JSON came back. Common causes: (1) **Vite dev proxy** timing out on long runs — `ui/vite.config.ts` sets no proxy timeout for `/api`; (2) **`uvicorn --reload`** restarting when files under `outputs/` change — the API now writes `creators.json` / `.csv` **after** sending the response so reload does not cut off the request.

**Headline engagement % (UI):** `headline_engagement_floor` in `config/settings.yaml` is **off by default** so you keep more creators; turn `enabled: true` and set `min_fraction` (e.g. `0.06` = 6%) if you want a hard cut on TikTok/YouTube after scoring. Tight vs loose engagement is mostly `engagement_quality_gate` (follower + view floors).
