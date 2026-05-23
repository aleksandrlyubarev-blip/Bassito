"""
Bassito LongLive-2.0 HTTP service (FastAPI + SSE).

Stands up on the Blackwell GPU node. The Filmitto control plane
(romeo_phd) and the bassito Telegram bot both call this service; it is
the single source of NVFP4 inference and serializes GPU access with an
asyncio.Lock so concurrent requests never collide on the model.

Endpoints:
  POST /v1/longlive/generate     submit a multi-shot generation job
  POST /v1/longlive/extend       extend an existing clip
  POST /v1/longlive/restyle      restyle an existing clip
  GET  /v1/jobs/{job_id}         poll job status
  GET  /v1/jobs/{job_id}/stream  SSE: ChunkEvent per decoded chunk + done
  GET  /v1/health                liveness + Blackwell capability report

Run with:
    uvicorn bassito_longlive_service:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from longlive_engine import (
    BlackwellRequiredError,
    ChunkEvent,
    LongLiveEngine,
    MultiShotRunner,
    ShotSpec,
    ensure_blackwell,
)

logger = logging.getLogger("bassito.longlive.service")
logging.basicConfig(
    level=os.getenv("BASSITO_LONGLIVE_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

OUTPUT_ROOT = Path(os.getenv("BASSITO_LONGLIVE_OUTPUT", "output/longlive"))
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

_gpu_lock = asyncio.Lock()
_engine: Optional[LongLiveEngine] = None
_jobs: dict[str, "_JobState"] = {}


class _JobState:
    def __init__(self, job_id: str, shots: list[ShotSpec]):
        self.job_id = job_id
        self.shots = shots
        self.queue: asyncio.Queue[Optional[ChunkEvent]] = asyncio.Queue()
        self.status: str = "queued"
        self.error: Optional[str] = None
        self.long_video_path: Optional[str] = None

    def to_status_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "long_video_path": self.long_video_path,
            "error": self.error,
            "shot_count": len(self.shots),
        }


class ShotInput(BaseModel):
    prompt: str
    duration_seconds: float = 4.0
    reference_image_path: Optional[str] = None
    reference_clip_path: Optional[str] = None
    shot_id: Optional[str] = None

    def to_spec(self) -> ShotSpec:
        data = self.model_dump()
        data["shot_id"] = data.get("shot_id") or ""
        return ShotSpec(**data)


class GenerateRequest(BaseModel):
    prompt: str = Field(..., description="High-level film prompt; used when shots are omitted")
    shots: list[ShotInput] = Field(default_factory=list)


class ExtendRequest(BaseModel):
    source_clip_path: str
    prompt: str
    duration_seconds: float = 4.0


class RestyleRequest(BaseModel):
    source_clip_path: str
    style_prompt: str
    duration_seconds: Optional[float] = None


class JobAccepted(BaseModel):
    job_id: str
    status: str
    stream_url: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _engine
    try:
        ensure_blackwell()
        _engine = LongLiveEngine.get()
        logger.info("LongLive-2.0 engine ready on Blackwell")
    except BlackwellRequiredError as exc:
        logger.warning("LongLive-2.0 engine NOT ready: %s", exc)
    yield


app = FastAPI(title="Bassito LongLive-2.0", version="0.1.0", lifespan=lifespan)


@app.get("/v1/health")
async def health() -> dict:
    info: dict = {
        "service": "bassito-longlive",
        "engine_loaded": False,
        "blackwell": False,
    }
    try:
        cap = ensure_blackwell()
        info["blackwell"] = True
        info["compute_capability"] = f"{cap[0]}.{cap[1]}"
    except BlackwellRequiredError as exc:
        info["error"] = str(exc)
    if _engine is not None:
        info["engine_loaded"] = _engine.loaded
    return info


def _ensure_engine() -> LongLiveEngine:
    if _engine is None:
        raise HTTPException(
            status_code=503,
            detail="LongLive engine not initialized. GET /v1/health for diagnostics.",
        )
    return _engine


def _new_job(shots: list[ShotSpec]) -> _JobState:
    job_id = f"ll_{uuid.uuid4().hex[:10]}"
    state = _JobState(job_id, shots)
    _jobs[job_id] = state
    return state


async def _run_job(state: _JobState) -> None:
    state.status = "running"
    try:
        async with _gpu_lock:
            engine = _ensure_engine()
            runner = MultiShotRunner(engine, output_dir=OUTPUT_ROOT / state.job_id)
            async for event in runner.stream(state.job_id, state.shots):
                await state.queue.put(event)
            state.long_video_path = str(
                OUTPUT_ROOT / state.job_id / f"{state.job_id}_long.mp4"
            )
        state.status = "completed"
    except Exception as exc:
        state.status = "failed"
        state.error = str(exc)
        logger.exception("LongLive job %s failed", state.job_id)
    finally:
        await state.queue.put(None)


@app.post("/v1/longlive/generate", response_model=JobAccepted, status_code=202)
async def generate(req: GenerateRequest) -> JobAccepted:
    _ensure_engine()
    shots = [s.to_spec() for s in req.shots] if req.shots else [ShotSpec(prompt=req.prompt)]
    state = _new_job(shots)
    asyncio.create_task(_run_job(state), name=f"longlive-{state.job_id}")
    return JobAccepted(
        job_id=state.job_id,
        status=state.status,
        stream_url=f"/v1/jobs/{state.job_id}/stream",
    )


@app.post("/v1/longlive/extend", response_model=JobAccepted, status_code=202)
async def extend(req: ExtendRequest) -> JobAccepted:
    _ensure_engine()
    shots = [ShotSpec(
        prompt=req.prompt,
        duration_seconds=req.duration_seconds,
        reference_clip_path=req.source_clip_path,
    )]
    state = _new_job(shots)
    asyncio.create_task(_run_job(state), name=f"longlive-extend-{state.job_id}")
    return JobAccepted(
        job_id=state.job_id,
        status=state.status,
        stream_url=f"/v1/jobs/{state.job_id}/stream",
    )


@app.post("/v1/longlive/restyle", response_model=JobAccepted, status_code=202)
async def restyle(req: RestyleRequest) -> JobAccepted:
    _ensure_engine()
    shots = [ShotSpec(
        prompt=req.style_prompt,
        duration_seconds=req.duration_seconds or 4.0,
        reference_clip_path=req.source_clip_path,
    )]
    state = _new_job(shots)
    asyncio.create_task(_run_job(state), name=f"longlive-restyle-{state.job_id}")
    return JobAccepted(
        job_id=state.job_id,
        status=state.status,
        stream_url=f"/v1/jobs/{state.job_id}/stream",
    )


@app.get("/v1/jobs/{job_id}")
async def job_status(job_id: str) -> dict:
    state = _jobs.get(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    return state.to_status_dict()


@app.get("/v1/jobs/{job_id}/stream")
async def stream_job(job_id: str) -> EventSourceResponse:
    state = _jobs.get(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    async def event_source() -> AsyncIterator[dict]:
        while True:
            event = await state.queue.get()
            if event is None:
                yield {
                    "event": "done",
                    "data": json.dumps(state.to_status_dict()),
                }
                return
            yield {"event": "chunk", "data": json.dumps(asdict(event))}

    return EventSourceResponse(event_source())
