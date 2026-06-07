"""
Local image-to-video pipeline for MacBook Pro M5 Pro (24 GB unified memory).

Two strictly-sequential stages, because the keyframe model and the video model
cannot be co-resident in ~18-20 GB usable:

    1. Ideogram 4 (nf4)  ->  perfect first frame (keyframe)
    2. offload Ideogram 4 (free the unified pool)
    3. I2V backend (Wan 2.1 / LTX-Video / HunyuanVideo 1.5) -> animate keyframe

`M5VideoPipeline.run` plans the offload against a `MemoryBudget`, drives each
stage, and returns an `I2VResult`. The planning, stage ordering and offload
sequencing are pure and fully testable; the model `load`/`generate`/`animate`
calls are the gated integration points inside each engine.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .i2v_models import BaseI2VBackend, I2VRequest, select_backend
from .ideogram4 import Ideogram4Config, Ideogram4KeyframeGenerator, JSONPrompt
from .unified_memory import (
    AppleSiliconRequiredError,
    MemoryBudget,
    OffloadPlan,
    ensure_apple_silicon,
)

logger = logging.getLogger("bassito.m5.pipeline")


@dataclass(slots=True)
class M5VideoConfig:
    """Top-level config for the local M5 I2V pipeline."""

    budget: MemoryBudget = field(default_factory=MemoryBudget)
    ideogram: Optional[Ideogram4Config] = None
    i2v_backend: Optional[str] = None     # force a backend by name, else auto
    num_frames: int = 81
    fps: int = 16
    width: int = 832
    height: int = 480

    @classmethod
    def from_env(cls) -> "M5VideoConfig":
        return cls(
            budget=MemoryBudget.from_env(),
            i2v_backend=os.getenv("BASSITO_M5_I2V_BACKEND") or None,
            num_frames=int(os.getenv("BASSITO_M5_FRAMES", "81")),
            fps=int(os.getenv("BASSITO_M5_FPS", "16")),
            width=int(os.getenv("BASSITO_M5_WIDTH", "832")),
            height=int(os.getenv("BASSITO_M5_HEIGHT", "480")),
        )


@dataclass(slots=True)
class I2VResult:
    job_id: str
    keyframe_path: str
    video_path: str
    backend: str
    num_frames: int
    fps: int
    duration_seconds: float
    plan_notes: list[str] = field(default_factory=list)


class M5VideoPipeline:
    """
    Drives the keyframe -> offload -> animate sequence on Apple Silicon.

    Build once per process (it lazily reuses the Ideogram 4 singleton); call
    `run` per job. `plan` is exposed separately so callers (and tests) can
    inspect the memory decision without touching the models.
    """

    def __init__(self, config: Optional[M5VideoConfig] = None):
        self.config = config or M5VideoConfig.from_env()

    def select_i2v(self) -> BaseI2VBackend:
        """Choose the I2V backend for the current budget / preference."""
        return select_backend(self.config.budget, prefer=self.config.i2v_backend)

    def plan(self, backend: Optional[BaseI2VBackend] = None) -> OffloadPlan:
        """
        Plan the two stages against the memory budget.

        Returns an OffloadPlan whose `sequential` flag will be True on a 24 GB
        part (Ideogram 4 + a heavy I2V model cannot co-exist), which is exactly
        why `run` offloads stage 1 before loading stage 2.
        """
        ideogram_cfg = self.config.ideogram or _maybe_ideogram_config()
        backend = backend or self.select_i2v()
        stages = []
        if ideogram_cfg is not None:
            stages.append(ideogram_cfg.footprint)
        stages.append(backend.footprint)
        return OffloadPlan.plan(self.config.budget, stages)

    def run(
        self,
        job_id: str,
        prompt: JSONPrompt | str,
        motion_prompt: str,
        output_dir: Path,
    ) -> I2VResult:
        """
        Generate a keyframe with Ideogram 4, offload it, then animate it.

        `prompt` conditions the keyframe (raw caption or structured JSONPrompt);
        `motion_prompt` describes the motion the I2V backend extrapolates.
        """
        ensure_apple_silicon()
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        backend = self.select_i2v()
        plan = self.plan(backend)
        for note in plan.notes:
            logger.info("[%s] plan: %s", job_id, note)

        # ── Stage 1: Ideogram 4 keyframe ──────────────────────────────
        keyframe_gen = Ideogram4KeyframeGenerator.get(self.config.ideogram)
        keyframe_path = output_dir / f"{job_id}_keyframe.png"
        keyframe_gen.generate(prompt, keyframe_path)

        # ── Stage 2: offload before the video model claims the pool ───
        if plan.sequential:
            logger.info("[%s] offloading Ideogram 4 before I2V stage", job_id)
            keyframe_gen.unload()

        # ── Stage 3: animate the keyframe ─────────────────────────────
        video_path = output_dir / f"{job_id}_i2v.mp4"
        request = I2VRequest(
            keyframe_path=keyframe_path,
            motion_prompt=motion_prompt,
            out_path=video_path,
            num_frames=self.config.num_frames,
            fps=self.config.fps,
            width=self.config.width,
            height=self.config.height,
        )
        backend.animate(request)
        backend.unload()

        return I2VResult(
            job_id=job_id,
            keyframe_path=str(keyframe_path),
            video_path=str(video_path),
            backend=backend.NAME,
            num_frames=request.num_frames,
            fps=request.fps,
            duration_seconds=request.duration_seconds,
            plan_notes=plan.notes,
        )


def _maybe_ideogram_config() -> Optional[Ideogram4Config]:
    """Return an Ideogram4Config from env if configured, else None.

    Lets `plan` include the keyframe footprint when weights are configured,
    while still being callable (for the I2V-only footprint) when they are not.
    """
    try:
        return Ideogram4Config.from_env()
    except AppleSiliconRequiredError:
        return None
