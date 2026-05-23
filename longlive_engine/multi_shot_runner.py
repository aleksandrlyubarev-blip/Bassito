"""
Multi-shot autoregressive runner.

Coordinates: shot iteration -> NVFP4 transformer (GPU 0) -> sliding KV
cache -> async VAE decode (GPU 1) -> frame chunks -> concatenated
long-video file. Emits ChunkEvents for streaming consumers (SSE, Telegram).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Optional, Union

logger = logging.getLogger("bassito.longlive.runner")

ProgressCallback = Callable[["ChunkEvent"], Union[None, Awaitable[None]]]


@dataclass(slots=True)
class ShotSpec:
    """One shot in a multi-shot storyboard."""

    prompt: str
    duration_seconds: float = 4.0
    reference_image_path: Optional[str] = None     # first-frame conditioning
    reference_clip_path: Optional[str] = None      # extend / restyle source
    shot_id: str = ""

    def __post_init__(self) -> None:
        if not self.shot_id:
            object.__setattr__(self, "shot_id", f"shot_{uuid.uuid4().hex[:8]}")


@dataclass(slots=True)
class ChunkEvent:
    """Streaming event emitted as each chunk decodes."""

    shot_id: str
    chunk_index: int
    frames_done: int
    frames_total: int
    partial_video_path: Optional[str] = None


@dataclass(slots=True)
class GenerationResult:
    job_id: str
    long_video_path: str
    shot_paths: list[str] = field(default_factory=list)
    total_frames: int = 0
    total_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)


class MultiShotRunner:
    """
    Drives the LongLive-2.0 engine across a list of shots, emitting chunk
    events for streaming consumers and producing a final concatenated video.
    """

    def __init__(self, engine, output_dir: Path):
        self.engine = engine
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def run(
        self,
        job_id: str,
        shots: list[ShotSpec],
        on_chunk: Optional[ProgressCallback] = None,
    ) -> GenerationResult:
        """Run all shots; invoke on_chunk for each ChunkEvent; return result."""
        if not shots:
            raise ValueError("At least one ShotSpec is required")
        if not self.engine.loaded:
            self.engine.load()

        result = self._new_result(job_id)
        async for event in self._generate(job_id, shots, result):
            if on_chunk is not None:
                await _maybe_await(on_chunk(event))
        return result

    async def stream(
        self,
        job_id: str,
        shots: list[ShotSpec],
    ) -> AsyncIterator[ChunkEvent]:
        """Async generator variant: yield ChunkEvents directly."""
        if not shots:
            raise ValueError("At least one ShotSpec is required")
        if not self.engine.loaded:
            self.engine.load()

        result = self._new_result(job_id)
        async for event in self._generate(job_id, shots, result):
            yield event

    def _new_result(self, job_id: str) -> GenerationResult:
        return GenerationResult(
            job_id=job_id,
            long_video_path=str(self.output_dir / f"{job_id}_long.mp4"),
        )

    async def _generate(
        self,
        job_id: str,
        shots: list[ShotSpec],
        result: GenerationResult,
    ) -> AsyncIterator[ChunkEvent]:
        """
        Core autoregressive loop.

        Integration point for the upstream LongLive-2.0 release. Once
        weights are loaded this method drives the real denoising loop:
          - tokenize each shot prompt
          - sample chunked latents under the sliding NVFP4 KV cache
          - submit latents to AsyncVAEDecoder on GPU 1
          - concatenate decoded frame chunks into per-shot mp4 segments
          - pass last-frame conditioning into the next shot so cuts are
            seamless (the "multi-shot" property in the LongLive paper)
          - mux all shot segments into result.long_video_path with ffmpeg

        The MultiShotRunner interface (run / stream / ChunkEvent) is the
        stable contract callers depend on; this is the only place that
        needs to be wired to the upstream API.
        """
        raise NotImplementedError(
            "Autoregressive multi-shot generation requires the upstream "
            "LongLive-2.0 weights and CUDA kernels. MultiShotRunner._generate "
            "is the integration point."
        )
        yield  # pragma: no cover  (makes this an async generator)


async def _maybe_await(value):
    if asyncio.iscoroutine(value):
        await value
