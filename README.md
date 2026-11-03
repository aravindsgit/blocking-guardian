# Blocking Session Guardian

A simulator for the "blocking session" problem in databases. It builds a chain of invented sessions,
finds the root blocker, applies a written safety policy, and recommends: keep waiting, hand over to a DBA,
or end the idle root session. A human approves risky actions, auto mode runs only low-risk ones, an optional
AI review can only add caution, and every step is written to an audit log.

Everything is simulated. No real database is ever contacted, and no real data is used.

## Run locally
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements-dev.txt
    uvicorn app.main:app --reload     # http://127.0.0.1:8000  (login admin / admin)

## Test
    python -m pytest -q
    ruff check .

## Deploy (Render + Neon)
Set ENV=production, DATABASE_URL, APP_USERNAME and APP_PASSWORD (12+ characters). Optional: ANTHROPIC_API_KEY
and LLM_MODEL. See render.yaml. Health check: /health.
