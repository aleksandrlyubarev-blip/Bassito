"""
Mock LongLive-2.0 backend for UX / plumbing testing without Blackwell.

Activated by setting `BASSITO_LONGLIVE_MOCK=1` in the environment. When
active:
- `MockLongLiveEngine` bypasses the Blackwell capability check and the
  NVFP4 weight loader (no real GPU touched).
- `MockMultiShotRunner` emits synthetic `ChunkEvent`s on a configurable
  cadence and writes empty placeholder `.mp4` files so the rest of the
  Filmitto stack (FastAPI service, romeo_phd routes, React UI) is
  exercisable end-to-end on a CPU-only Cloud Run / e2-micro instance.

This is **for plumbing and UX verification only**. The output files are
zero-byte placeholders, not real video frames. Swap in the real engine
when the upstream LongLive-2.0 release ships (or wire a different open
backend like Wan2.1 / HunyuanVideo for non-Blackwell GPUs).

Tunables:
  BASSITO_LONGLIVE_MOCK                 enable flag (1/true/yes/on)
  BASSITO_LONGLIVE_MOCK_CHUNK_MS        delay between chunks (default 400)
  BASSITO_LONGLIVE_MOCK_CHUNKS_PER_SHOT chunks per shot   (default 5)
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import AsyncIterator

from .multi_shot_runner import (
    ChunkEvent,
    GenerationResult,
    MultiShotRunner,
    ShotSpec,
)
from .nvfp4_model import LongLiveEngine

logger = logging.getLogger("bassito.longlive.mock")

MOCK_ENV_VAR = "BASSITO_LONGLIVE_MOCK"
MOCK_FPS = 24


def is_mock_enabled() -> bool:
    """True when the BASSITO_LONGLIVE_MOCK env var is set to a truthy value."""
    return os.getenv(MOCK_ENV_VAR, "").lower() in ("1", "true", "yes", "on")


def _mock_chunk_delay_seconds() -> float:
    return max(0.0, int(os.getenv("BASSITO_LONGLIVE_MOCK_CHUNK_MS", "400"))) / 1000.0


def _mock_chunks_per_shot() -> int:
    return max(1, int(os.getenv("BASSITO_LONGLIVE_MOCK_CHUNKS_PER_SHOT", "5")))


class MockLongLiveEngine(LongLiveEngine):
    """Drop-in LongLiveEngine that skips Blackwell + weight loading."""

    def __init__(self) -> None:
        # Deliberately skip super().__init__ — it calls ensure_blackwell()
        # and LongLiveConfig.from_env(), both of which would fail here.
        self.config = None  # type: ignore[assignment]
        self._model = "mock-model"  # type: ignore[assignment]
        self._vae = "mock-vae"  # type: ignore[assignment]

    def load(self) -> None:
        logger.info("MockLongLiveEngine.load(): no-op (BASSITO_LONGLIVE_MOCK enabled)")

    @property
    def loaded(self) -> bool:  # type: ignore[override]
        return True


class MockMultiShotRunner(MultiShotRunner):
    """MultiShotRunner that emits synthetic chunks on a timer."""

    async def _generate(
        self,
        job_id: str,
        shots: list[ShotSpec],
        result: GenerationResult,
    ) -> AsyncIterator[ChunkEvent]:
        delay = _mock_chunk_delay_seconds()
        chunks_per_shot = _mock_chunks_per_shot()

        for shot in shots:
            frames_total = max(1, int((shot.duration_seconds or 4.0) * MOCK_FPS))
            for chunk_index in range(chunks_per_shot):
                if delay > 0:
                    await asyncio.sleep(delay)
                frames_done = int(frames_total * (chunk_index + 1) / chunks_per_shot)
                yield ChunkEvent(
                    shot_id=shot.shot_id,
                    chunk_index=chunk_index,
                    frames_done=frames_done,
                    frames_total=frames_total,
                )
            shot_path = self.output_dir / f"{job_id}_{shot.shot_id}.mp4"
            shot_path.touch()
            result.shot_paths.append(str(shot_path))
            result.total_frames += frames_total
            result.total_seconds += shot.duration_seconds or 4.0

        Path(result.long_video_path).touch()
        result.warnings.append(
            "Mock backend — placeholder mp4 only, no real frames generated."
        )


def make_engine() -> LongLiveEngine:
    """
    Factory: returns the mock engine when BASSITO_LONGLIVE_MOCK is enabled,
    otherwise the real lazy-loading LongLiveEngine singleton.
    """
    if is_mock_enabled():
        logger.warning(
            "BASSITO_LONGLIVE_MOCK=1 — using MockLongLiveEngine. "
            "Output videos will be empty placeholders."
        )
        return MockLongLiveEngine()
    return LongLiveEngine.get()


def make_runner(engine: LongLiveEngine, output_dir: Path) -> MultiShotRunner:
    """
    Factory: returns MockMultiShotRunner when mock mode is enabled,
    otherwise the real MultiShotRunner. Pair with make_engine() above
    so callers don't have to think about the env var.
    """
    if is_mock_enabled():
        return MockMultiShotRunner(engine, output_dir)
    return MultiShotRunner(engine, output_dir)
