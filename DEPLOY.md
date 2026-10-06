# Deploying Placecard for free

The hackathon requires a public live demo URL that judges can use for free, plus a public repo with an open-source licence. This guide covers both. Everything below was checked on 2026-10-05; free tiers change, so re-check the linked pages before relying on them.

## 1. Which free host?

| Host | Free for a Python web app? | Sleeps when idle? | Card needed? | Verdict |
|---|---|---|---|---|
| **Render** free web service | Yes. 750 instance-hours/month per workspace, enough for one service running all month. Small instance (roughly 512 MB, shared CPU), plenty for this stdlib app. | Yes, after 15 minutes without traffic. The first request then takes about 1 minute. | No ([render.com/docs/free](https://render.com/docs/free)) | **Recommended.** `render.yaml` included. |
| Hugging Face Spaces (Docker) | **No, not any more.** HF's docs now say Gradio and Docker Spaces "require a paid plan to create" (PRO). Only static Spaces are free, and they can't run our backend. | n/a | PRO plan | Ruled out. |
| Koyeb | Its pricing page lists no free web-service instance (only a free Postgres allowance). | n/a | Yes | Ruled out. |
| Fly.io, Railway, Google Cloud Run | Trial credit or pay-as-you-go, and a billing card is required. | varies | Yes | Paid fallback only. |
| PythonAnywhere (free) | Outbound internet is limited to an allow-list, so calls to `hackathon.api.qloo.com` would be blocked. | n/a | No | Ruled out. |

The plan is **Render free**, plus a free uptime pinger during judging so judges never hit a cold start (step 4). The `Dockerfile` works on any paid container host if Render ever fails you.

## 2. Make the public GitHub repo (owner, about 5 minutes)

The project folder `hackathon/placecard/` is self-contained (README, MIT LICENSE, .gitignore, tests). Publish it as its own public repo:

1. On github.com, click **+** (top right), then **New repository**.
2. Name it, for example `placecard`. Set it to **Public**. Do **not** add a README, licence or .gitignore (the folder has them). Click **Create repository**.
3. On your PC, in Git Bash (replace `YOUR-USER`):
   ```bash
   cp -r C:/Users/User/rose-hackathon/hackathon/placecard C:/Users/User/placecard-public
   cd C:/Users/User/placecard-public
   rm -rf __pycache__ */__pycache__ */*/__pycache__
   git init -b main
   git add .
   git status            # check: no .env file and no keys listed
   git commit -m "Placecard: Qloo-powered seating-chart agent"
   git remote add origin https://github.com/YOUR-USER/placecard.git
   git push -u origin main
   ```
   For later updates, copy the changed files across again (or ask Claude Code to sync them), then commit and push.
4. On the repo page, click the gear next to **About**. Add a description ("AI agent that seats dinners, weddings and conference tables so nobody sits with only strangers, powered by Qloo taste graph") and the website (your Render URL, once you have it). GitHub detects the MIT licence automatically and shows it in About, which the rules require.

## 3. Deploy on Render (click by click)

1. Go to <https://dashboard.render.com/register> and sign up with **GitHub** (free, no card).
2. Click **New +** (top right), then **Web Service**.
3. Choose **Build and deploy from a Git repository**. Connect GitHub if asked, give Render access to the `placecard` repo, and click **Connect** next to it.
4. Fill in the form:
   - **Name:** `placecard` (your URL becomes `https://placecard-xxxx.onrender.com`)
   - **Region:** **Singapore** (closest to Malaysia)
   - **Branch:** `main`
   - **Root Directory:** leave empty
   - **Runtime / Language:** **Python 3**
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python -m placecard.server`
   - **Instance Type:** **Free**
5. Scroll to **Environment Variables** and click **Add Environment Variable** for each row below. Values are stored encrypted and never appear in the repo:

   | Key | Value |
   |---|---|
   | `PYTHON_VERSION` | `3.12.7` |
   | `QLOO_API_KEY` | your Qloo hackathon key |
   | `LLM_PROVIDER` | `gemini` (or `anthropic`) |
   | `GEMINI_API_KEY` | your Google AI Studio key (if using Gemini) |
   | `ANTHROPIC_API_KEY` | your Anthropic key (if using Claude) |
   | `RUNS_PER_DAY` | `120` (a global cap on agent runs per day, so the demo can't drain your quota; the Qloo hackathon key allows only 10,000 requests a month and a cold 40-guest run costs about 145) |
   | `MAX_QLOO_CALLS` | `600` (optional: cap on Qloo requests per run; a guest costs about 3.5 requests on a cold cache (40 guests: 143, 24 guests: 89), repeated favourites are cached) |

   You don't set `PORT`. Render sets it and the app reads it.
6. Click **Deploy Web Service**. Watch the log until it shows `Placecard on http://0.0.0.0:10000 qloo=live llm=gemini:...`.
7. Open `https://<your-service>.onrender.com/api/health`. It must show `"qloo": "live"` and the expected `llm_provider`. If it says `sample` or `mock`, a key is missing: go to **Environment**, fix it, then **Save, rebuild, and deploy**.
8. Open the site and press **Seat my guests**. The header pills must say **Qloo: live**, and there must be **no SAMPLE banner**.

**Alternative: Blueprint.** Choose **New +**, then **Blueprint**, and pick the repo. Render reads `render.yaml` and asks for the three secret values. The result is the same as steps 2 to 6.

**Changing keys later:** open the service, go to **Environment**, edit the value, then **Save, rebuild, and deploy**. Each push to `main` redeploys automatically.

## 4. Keep it awake during judging (judging runs Nov 2-16, 2026)

A free Render service sleeps after 15 idle minutes, and waking it takes about a minute. That is a bad first impression for a judge. Keeping it awake all month uses about 744 of the 750 free hours. Steps:

1. Sign up free at <https://cron-job.org> (or UptimeRobot).
2. Create a job that runs `GET https://<your-service>.onrender.com/api/health` **every 10 minutes**.
3. Enable it from about Oct 28 until Nov 17. `/api/health` makes no LLM or Qloo calls, so pinging it costs nothing.

## 5. Running the container anywhere else

```bash
docker build -t placecard .
docker run -p 8000:8000 -e QLOO_API_KEY=... -e GEMINI_API_KEY=... -e LLM_PROVIDER=gemini placecard
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| Agent error "Qloo rejected the API key (401)" | Check `QLOO_API_KEY`. Hackathon keys only work on `https://hackathon.api.qloo.com`, which is the default `QLOO_BASE_URL`. Don't override it. |
| Many guests show "no taste data" or the agent asks about unmatched favourites | Qloo's hackathon dataset may not know a favourite under that spelling. Open `raw tool result` under `build_taste_profiles` to see which ones, and try the exact title. If a guest's profile comes back empty although their favourites matched, check the taste-analysis payload shape in `placecard/qloo.py` (`_norm_tag`, `_tag_vector`). |
| "The Qloo call budget for this run is used up" | A big party with many distinct favourites needs about 3.5 requests per guest on a cold cache. Raise `MAX_QLOO_CALLS`. Guests left unprofiled are seated with a neutral score. |
| "These constraints cannot all be met" | Two requirements contradict each other or do not fit the table sizes (for example five people who must sit together at tables of four). The agent reports which requirement it dropped. |
| "Gemini rate limit or free-tier quota reached (429)" | Free-tier limits are per Google Cloud project; see them at <https://aistudio.google.com/rate-limit>. Wait a minute, set `GEMINI_MODEL=gemini-3.1-flash-lite` (a separate daily quota; `gemini-3.5-flash` allows a limited number of free requests per day, per project), or switch to Claude (`LLM_PROVIDER=anthropic`). |
| Timeline appears all at once instead of live | A proxy is buffering the stream. Results are still correct. |
| "The monthly Qloo quota is nearly used up" | The hackathon key allows 10,000 requests a month (5 per second). `/api/health` shows `qloo_quota` once a run has made a request. The app stops spending when `QLOO_MONTH_RESERVE` (default 100) requests remain, and seats with the profiles it has. Keep `RUNS_PER_DAY` low and use the preset lists for demos: repeated favourites are cached for 24 hours per process. |
| "daily run limit is reached" | Raise `RUNS_PER_DAY` in Render's Environment settings. |
