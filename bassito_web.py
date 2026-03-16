"""
Bassito Web Frontend
=====================
Public-facing FastAPI app: submit jobs, track progress, view queue.
Access is gated by a WEB_ACCESS_KEY set in .env.

Routes:
    GET  /            Landing page + submission form
    POST /generate    Submit a new video generation job
    GET  /jobs        Queue overview
    GET  /jobs/{id}   Per-job status & phase progress
    GET  /health      Simple health check (no auth)
"""

import os
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("bassito.web")

WEB_ACCESS_KEY = os.getenv("WEB_ACCESS_KEY", "")
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))

# Populated by the orchestrator before the web server starts
_job_queue = None  # type: ignore


def set_job_queue(queue):
    global _job_queue
    _job_queue = queue


app = FastAPI(title="Bassito", docs_url=None, redoc_url=None)

_here = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(_here / "static")), name="static")
templates = Jinja2Templates(directory=str(_here / "templates"))


# ── Auth helpers ─────────────────────────────────────────────────────

def _check_key(key: str) -> bool:
    """Return True if the provided access key matches (or no key is configured)."""
    if not WEB_ACCESS_KEY:
        return True
    return key == WEB_ACCESS_KEY


# ── Routes ───────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, error: Optional[str] = None):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "error": error, "key_required": bool(WEB_ACCESS_KEY)},
    )


@app.post("/generate")
async def generate(
    request: Request,
    prompt: str = Form(...),
    access_key: str = Form(""),
):
    if not _check_key(access_key):
        return RedirectResponse("/?error=Invalid+access+key", status_code=303)

    if not prompt.strip():
        return RedirectResponse("/?error=Prompt+cannot+be+empty", status_code=303)

    if _job_queue is None:
        raise HTTPException(status_code=503, detail="Pipeline not ready")

    if _job_queue.pending_count >= _job_queue._queue.maxsize:
        return RedirectResponse("/?error=Queue+is+full%2C+try+again+later", status_code=303)

    # Use a synthetic chat_id of 0 for web-submitted jobs
    job = _job_queue.create_job(prompt=prompt.strip(), chat_id=0)
    await _job_queue.enqueue(job)
    logger.info(f"Web job submitted: {job.id} — {job.prompt[:60]}")

    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


@app.get("/jobs", response_class=HTMLResponse)
async def queue_view(request: Request):
    jobs = list(_job_queue._jobs.values()) if _job_queue else []
    jobs_sorted = sorted(jobs, key=lambda j: j.created_at, reverse=True)
    return templates.TemplateResponse(
        "queue.html",
        {"request": request, "jobs": jobs_sorted},
    )


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: str):
    if _job_queue is None or job_id not in _job_queue._jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = _job_queue._jobs[job_id]

    from bassito_telegram_orchestrator import PipelinePhase
    all_phases = list(PipelinePhase)

    return templates.TemplateResponse(
        "job.html",
        {
            "request": request,
            "job": job,
            "all_phases": all_phases,
            "refresh": job.status.value in ("queued", "running"),
        },
    )
