# Sift Dashboard

A full-stack version of Sift: a FastAPI backend (Python) that does the
fetching/interpreting/scoring, and a Next.js frontend (React + TypeScript
+ Tailwind) that gives you an actual UI to drive it.

## Structure

```
sift-app/
  backend/     FastAPI API - interpret + score endpoints
  frontend/    Next.js dashboard - input, subreddit review, results table
```

## Backend setup

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export DEEPSEEK_API_KEY="your-key-here"
export REDDIT_USER_AGENT="python:sift:v1.0 (by /u/your_reddit_username)"

uvicorn main:app --reload --port 8000
```

Leave this running in its own terminal tab. Visit
`http://localhost:8000/docs` to see the interactive API docs FastAPI
generates automatically.

## Frontend setup

In a **second** terminal tab:

```bash
cd frontend
npm install
npm run dev
```

Visit `http://localhost:3000` - that's the actual dashboard.

## How it works

1. **Input** - type a free-form description of what you're trying to find
   (no need to separately specify a "domain" or "rubric" - the backend
   derives both from your text).
2. **Subreddit review** - the backend suggests candidate subreddits based
   on your description. Add, edit, or remove any before continuing. Common
   forms such as `relationships`, `r/relationships`, and Reddit URLs are
   normalized automatically.
3. **Results** - the backend fetches recent posts from each subreddit via
   RSS, scores every one against the derived rubric, and the dashboard
   shows a ranked table: score, title, reason, link.

## Notes

- Both servers need to be running at the same time (backend on :8000,
  frontend on :3000) for the dashboard to work.
- The DeepSeek API key only ever lives on the backend - it's never sent
  to or stored in the browser.
- A Phase 1 scan is intentionally limited to 5 subreddits and 5 posts per
  subreddit. This keeps response time and model cost predictable while the
  relevance workflow is being validated.
- Reddit may rate-limit RSS traffic. Sift spaces feed requests and retries
  HTTP 429 responses with bounded backoff. Set `REDDIT_USER_AGENT` to a
  descriptive value containing your Reddit username.
- Set `NEXT_PUBLIC_API_BASE_URL` for a non-local backend. Set the backend's
  comma-separated `SIFT_ALLOWED_ORIGINS` variable for non-local frontends.
- No data persists between runs yet - refreshing or restarting starts
  fresh. Persistence, scheduling, and push notifications are later
  phases (v2+ in the original project plan), not part of this dashboard.
