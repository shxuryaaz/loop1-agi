# Agilow — Loop 1: Sprint Goals Generator

A small full-stack app for the Agilow project management team. Upload a meeting
transcript (`.txt`), and a two-phase OpenAI pipeline (extraction → formatting)
produces a clean, downloadable Markdown **sprint goals** document.

- **Backend:** Python + FastAPI, OpenAI `gpt-4o` (temperature 0.2), `tenacity` retries
- **Frontend:** React (Vite), plain CSS, `react-markdown`

---

## Prerequisites

- Python 3.10+
- Node.js 18+
- An OpenAI API key

---

## 1. Backend

```bash
cd backend
python -m venv .venv

# Activate the virtual environment:
#   PowerShell:  .venv\Scripts\Activate.ps1
#   bash/zsh:    source .venv/bin/activate

pip install -r requirements.txt
```

Set your OpenAI API key (the server **fails fast on startup** if it is missing):

```bash
# PowerShell
$env:OPENAI_API_KEY = "sk-..."

# bash/zsh
export OPENAI_API_KEY=sk-...
```

Run the server (on port 8000):

```bash
uvicorn main:app --reload
```

The API is now available at `http://localhost:8000`. CORS is configured to allow
the Vite dev server at `http://localhost:5173`.

### Endpoint

`POST /api/process` — multipart form data:

| Field          | Type            | Required | Notes                                  |
| -------------- | --------------- | -------- | -------------------------------------- |
| `file`         | file (`.txt`)   | yes      | The meeting transcript                 |
| `sprint_label` | string          | yes      | e.g. `June 6th - June 13th`            |
| `team_members` | string          | no       | Comma-separated names                  |

Returns JSON:

```json
{
  "markdown": "# Sprint Goals (...)\n...",
  "meeting_summary": "…",
  "people_count": 2
}
```

On a validation problem it returns `400` with `{ "error": "..." }`; on a pipeline
failure it returns `500` with `{ "error": "..." }` (full stack traces are logged
server-side, never returned to the client).

---

## 2. Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the printed URL (default `http://localhost:5173`).

---

## Using the app

1. Choose a `.txt` transcript file.
2. Enter a **Sprint label** (e.g. `June 6th - June 13th`).
3. Optionally enter **team members** (comma-separated).
4. Click **Generate**. A spinner shows while the pipeline runs.
5. The formatted sprint goals render on the page.
6. Click **Download .md** to save `sprint_goals_<sanitized_label>.md`.

A sample transcript is included at [`sample_transcript.txt`](sample_transcript.txt)
for a quick end-to-end test.

---

## Deployment (Render + Vercel)

### Backend → Render

1. Push this repo to GitHub.
2. In Render: **New + → Blueprint**, select the repo. Render reads
   [`render.yaml`](render.yaml) and creates the `agilow-loop1-backend` web
   service (root directory `backend`).
   - Or create a **Web Service** manually: root dir `backend`, build
     `pip install -r requirements.txt`, start
     `uvicorn main:app --host 0.0.0.0 --port $PORT`.
3. Set environment variables on the service:
   - `OPENAI_API_KEY` — your key (required; the server won't boot without it).
   - `ALLOWED_ORIGINS` — your Vercel URL, e.g. `https://your-app.vercel.app`
     (comma-separated for multiple; localhost is always allowed).
4. Deploy, then note the service URL, e.g. `https://agilow-loop1-backend.onrender.com`.

### Frontend → Vercel

1. In Vercel: **Add New → Project**, import the repo.
2. Set **Root Directory** to `frontend`. Vercel auto-detects Vite
   (build `npm run build`, output `dist`); [`frontend/vercel.json`](frontend/vercel.json)
   also declares this.
3. Add an Environment Variable:
   - `VITE_API_URL` — your Render backend URL (no trailing slash), e.g.
     `https://agilow-loop1-backend.onrender.com`.
4. Deploy. Then make sure the backend's `ALLOWED_ORIGINS` includes this Vercel
   URL and redeploy the backend if you changed it.

> Note: Render's free tier sleeps when idle, so the first request after a pause
> can take ~30–60s while the service wakes up.

## How it works

**Phase 1 — Extraction** (`extract_goals`): the transcript is sent to `gpt-4o`
with a system prompt that handles messy, multilingual transcripts and extracts
only grounded commitments into a strict JSON schema. Invalid JSON triggers one
corrective retry; transient API errors are retried up to 3 times with
exponential backoff (`tenacity`).

**Phase 2 — Formatting** (`format_sprint_doc`): the structured JSON is converted
into Markdown that follows an exact, worked-example structure (per-person
headings, numbered goals, points, subtasks, success criteria, dependencies,
risks, and per-person totals).
