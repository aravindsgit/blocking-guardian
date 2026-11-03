"""Blocking Session Guardian: server-rendered pages behind one login. Everything is simulated."""
import hmac
import os
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import llm
from .engine import MAX_AUTO_ROLLBACK_S, advance, apply_kill, auto_run, entry, evaluate
from .models import Case, SessionLocal, init_db
from .scenarios import SCENARIOS, generate
from .view import caution_roots, case_view

BASE = Path(__file__).parent
ENV = os.getenv("ENV", "development")
APP_USERNAME = os.getenv("APP_USERNAME", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD")
if ENV == "production" and (not APP_PASSWORD or len(APP_PASSWORD) < 12):
    raise RuntimeError("Set APP_PASSWORD (at least 12 characters) before running in production.")
APP_PASSWORD = APP_PASSWORD or "admin"  # local development only
REVIEW_LIMIT_PER_HOUR = int(os.getenv("REVIEW_LIMIT_PER_HOUR", "10"))
_review_calls = deque()
MODES = ("approval", "auto")
DEFAULTS = {"scenario": "idle_blocker", "mode": "approval", "seed": "7"}

app = FastAPI(title="Blocking Session Guardian")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
security = HTTPBasic()
init_db()


@app.middleware("http")
async def block_cross_site_posts(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method == "POST" and origin and urlparse(origin).netloc != request.headers.get("host", ""):
        return PlainTextResponse("Cross-site request blocked.", status_code=403)
    return await call_next(request)


def get_db():
    with SessionLocal() as db:
        yield db


def require_auth(credentials: HTTPBasicCredentials = Depends(security)):
    user_ok = hmac.compare_digest(credentials.username.encode(), APP_USERNAME.encode())
    pass_ok = hmac.compare_digest(credentials.password.encode(), APP_PASSWORD.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(401, "Incorrect username or password.", headers={"WWW-Authenticate": "Basic"})


async def form_data(request: Request) -> dict:
    return dict(await request.form())


def render(request: Request, name: str, status_code: int = 200, **context):
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def redirect(url: str):
    return RedirectResponse(url, status_code=303)


def review_allowed() -> bool:
    now = time.time()
    while _review_calls and now - _review_calls[0] > 3600:
        _review_calls.popleft()
    if len(_review_calls) >= REVIEW_LIMIT_PER_HOUR:
        return False
    _review_calls.append(now)
    return True


def get_case(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found.")
    return case


def pair_for(case: Case, root: int):
    chains, decisions = evaluate(case.sessions, set(case.rejected), caution_roots(case.reviews))
    for chain, decision in zip(chains, decisions):
        if chain["root"] == root:
            return chain, decision
    raise HTTPException(404, "No such blocking chain.")


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "database unavailable"}, status_code=503)
    return {"status": "ok"}


private = APIRouter(dependencies=[Depends(require_auth)])


def index_page(request, db, status_code=200, values=None, errors=None):
    cases = db.scalars(select(Case).order_by(Case.id.desc()).limit(50)).all()
    return render(request, "index.html", status_code=status_code, cases=cases, scenarios=SCENARIOS,
                  values=values or DEFAULTS, errors=errors or {})


@private.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    return index_page(request, db)


@private.post("/cases")
def create_case(request: Request, form: dict = Depends(form_data), db: Session = Depends(get_db)):
    errors = {}
    scenario, mode = str(form.get("scenario", "")), str(form.get("mode", ""))
    if scenario not in SCENARIOS:
        errors["scenario"] = "Choose one of the listed scenarios."
    if mode not in MODES:
        errors["mode"] = "Choose approval mode or auto mode."
    try:
        seed = int(str(form.get("seed", "")).strip() or 7)
        if not 0 <= seed <= 9999:
            raise ValueError
    except ValueError:
        errors["seed"] = "Enter a whole number from 0 to 9999."
    if errors:
        return index_page(request, db, 422, form, errors)
    audit = [entry("system", f"Case opened: {SCENARIOS[scenario]['title']} ({mode} mode, simulated data)")]
    sessions = auto_run(generate(scenario, seed), mode, set(), set(), audit)
    case = Case(scenario=scenario, title=SCENARIOS[scenario]["title"], mode=mode, seed=seed,
                sessions=sessions, rejected=[], reviews={}, audit=audit)
    db.add(case)
    db.commit()
    return redirect(f"/cases/{case.id}")


@private.get("/cases/{case_id}")
def view_case(request: Request, case_id: int, db: Session = Depends(get_db)):
    return render(request, "case.html", **case_view(get_case(db, case_id), llm_ready=llm.configured()))


@private.post("/cases/{case_id}/approve/{root}")
def approve(case_id: int, root: int, db: Session = Depends(get_db)):
    case = get_case(db, case_id)
    chain, decision = pair_for(case, root)
    if decision["action"] != "KILL_RECOMMENDED" or decision["state"] != "pending":
        raise HTTPException(409, "The policy does not allow ending this session.")
    case.sessions = apply_kill(case.sessions, chain)
    case.audit = case.audit + [entry("human", f"Approved: ended root session {root}; "
                                              f"{chain['blocked_count']} blocked sessions released (simulated)")]
    db.commit()
    return redirect(f"/cases/{case.id}")


@private.post("/cases/{case_id}/reject/{root}")
def reject(case_id: int, root: int, db: Session = Depends(get_db)):
    case = get_case(db, case_id)
    pair_for(case, root)
    case.rejected = sorted(set(case.rejected) | {root})
    case.audit = case.audit + [entry("human", f"Rejected the recommendation for root session {root}; nothing was done")]
    db.commit()
    return redirect(f"/cases/{case.id}")


@private.post("/cases/{case_id}/advance")
def advance_time(case_id: int, db: Session = Depends(get_db)):
    case = get_case(db, case_id)
    audit = list(case.audit) + [entry("system", "Time advanced by 5 minutes (simulated)")]
    sessions = advance(case.sessions, 300)
    case.sessions = auto_run(sessions, case.mode, set(case.rejected), caution_roots(case.reviews), audit)
    case.audit = audit
    db.commit()
    return redirect(f"/cases/{case.id}")


@private.post("/cases/{case_id}/mode")
def change_mode(case_id: int, form: dict = Depends(form_data), db: Session = Depends(get_db)):
    case = get_case(db, case_id)
    mode = str(form.get("mode", ""))
    if mode not in MODES:
        raise HTTPException(422, "Choose approval mode or auto mode.")
    audit = list(case.audit) + [entry("human", f"Mode changed to {mode}")]
    case.mode = mode
    case.sessions = auto_run(case.sessions, mode, set(case.rejected), caution_roots(case.reviews), audit)
    case.audit = audit
    db.commit()
    return redirect(f"/cases/{case.id}")


@private.post("/cases/{case_id}/review/{root}")
def ai_review(request: Request, case_id: int, root: int, db: Session = Depends(get_db)):
    case = get_case(db, case_id)
    chain, decision = pair_for(case, root)
    error = None
    if not llm.configured():
        error = "AI review is off because no ANTHROPIC_API_KEY is set. The policy decision stands on its own."
    elif not review_allowed():
        error = "The hourly limit for AI reviews was reached. Try again later."
    else:
        facts = {"decision": {"action": decision["action"], "requires_approval": decision["requires_approval"],
                              "rules": [r["text"] for r in decision["rules"]]},
                 "root_session": {k: next(s for s in case.sessions if s["sid"] == root)[k]
                                  for k in ("user", "program", "status", "idle_s", "txn_s", "rollback_s", "object")},
                 "blocked_count": chain["blocked_count"], "max_wait_s": chain["max_wait_s"],
                 "auto_rollback_limit_s": MAX_AUTO_ROLLBACK_S}
        try:
            result = llm.review(facts)
        except llm.LLMError as exc:
            error = str(exc)
        else:
            case.reviews = {**case.reviews, str(root): result}
            verdict = "agreed" if result["agree"] else "raised a concern, so a human must confirm"
            case.audit = case.audit + [entry("ai", f"Review of root session {root}: {verdict}")]
            db.commit()
    if error:
        return render(request, "case.html", **case_view(case, message=error, llm_ready=llm.configured()))
    return redirect(f"/cases/{case.id}")


@private.get("/cases/{case_id}/ticket.txt")
def ticket(case_id: int, db: Session = Depends(get_db)):
    case = get_case(db, case_id)
    body = case_view(case)["ticket"]
    return Response(body, media_type="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="ticket-update-{case.id}.txt"'})


@private.post("/cases/{case_id}/delete")
def delete_case(case_id: int, db: Session = Depends(get_db)):
    db.delete(get_case(db, case_id))
    db.commit()
    return redirect("/")


app.include_router(private)
