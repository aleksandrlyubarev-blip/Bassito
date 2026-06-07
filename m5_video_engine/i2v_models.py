"""
Image-to-Video backends for Apple Silicon, with memory-aware selection.

Three open-weights I2V families run locally on an M5 Pro 24 GB. Each animates
an Ideogram 4 keyframe by adding temporal attention over the spatial diffusion
process: the keyframe is VAE-encoded into latents, used as hard conditioning
for frame 0, and a motion prompt drives extrapolation across 49 / 81 / 129
frames.

| Backend            | Quant / runtime        | ~Resident | Notes                         |
|--------------------|------------------------|-----------|-------------------------------|
| Wan 2.1 14B        | 6-bit SVDQuant         | ~16 GB    | SOTA motion; fills 24 GB pool |
| Wan 2.1 1.3B       | SVDQuant               | ~4 GB     | Light; weaker spatial xforms  |
| LTX-Video (2.3)    | native Apple MLX       | ~8 GB     | Fastest on M-series           |
| HunyuanVideo 1.5   | Wan2GP / LightX2V      | ~6 GB     | Cinematic; great particles    |

Selection mirrors the CTA5 strategy pattern: backends declare a footprint and
a priority, and `select_backend` picks the highest-quality option that fits the
memory budget (or a forced choice). Backend construction and the fit logic are
pure and testable; only `load` / `animate` are gated integration points.
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .unified_memory import (
    AppleSiliconRequiredError,
    MemoryBudget,
    ModelFootprint,
    ensure_apple_silicon,
)

logger = logging.getLogger("bassito.m5.i2v")

# Frame counts the temporal models are trained to emit.
VALID_FRAME_COUNTS = (49, 81, 129)


@dataclass(slots=True)
class I2VRequest:
    """One image-to-video job."""

    keyframe_path: Path
    motion_prompt: str
    out_path: Path
    num_frames: int = 81
    fps: int = 16
    width: int = 832
    height: int = 480

    def __post_init__(self) -> None:
        if self.num_frames not in VALID_FRAME_COUNTS:
            raise ValueError(
                f"num_frames must be one of {VALID_FRAME_COUNTS}, got {self.num_frames}"
            )
        self.keyframe_path = Path(self.keyframe_path)
        self.out_path = Path(self.out_path)

    @property
    def duration_seconds(self) -> float:
        return self.num_frames / self.fps


class BaseI2VBackend(ABC):
    """
    Abstract image-to-video backend.

    Subclasses declare `NAME`, `QUANT`, `PRIORITY` (higher = preferred) and a
    `weights_gb`, and implement `load` / `animate`. Do not instantiate via the
    factory's private list — use `select_backend` / `force_backend`.
    """

    NAME: str = "base"
    QUANT: str = ""
    PRIORITY: int = 0
    WEIGHTS_GB: float = 0.0
    WORKING_GB: float = 3.0

    def __init__(self, weights_dir: Optional[Path] = None):
        self.weights_dir = Path(weights_dir) if weights_dir else None
        self._model = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def footprint(self) -> ModelFootprint:
        return ModelFootprint(
            name=self.NAME,
            quant=self.QUANT,
            weights_gb=self.WEIGHTS_GB,
            working_gb=self.WORKING_GB,
        )

    def fits(self, budget: MemoryBudget) -> bool:
        return self.footprint.resident_gb <= budget.usable_gb

    def unload(self) -> None:
        self._model = None
        logger.info("%s unloaded — unified-memory pool released", self.NAME)

    def _require_weights(self) -> Path:
        ensure_apple_silicon()
        if self.weights_dir is None or not self.weights_dir.exists():
            raise AppleSiliconRequiredError(
                f"{self.NAME} weights not found at {self.weights_dir}. "
                f"Set {self.ENV_WEIGHTS} to the unpacked checkpoint."
            )
        return self.weights_dir

    ENV_WEIGHTS: str = "BASSITO_I2V_WEIGHTS"

    @abstractmethod
    def load(self) -> None:
        """Load weights into unified memory via the backend's runtime."""

    @abstractmethod
    def animate(self, request: I2VRequest) -> Path:
        """Animate the keyframe into a clip; return the written path."""


class Wan21Backend(BaseI2VBackend):
    """Wan 2.1 14B (Alibaba Tongyi Wanxiang), 6-bit SVDQuant.

    The SOTA local choice for motion quality; at ~16 GB resident it nearly
    fills the M5 Pro 24 GB pool, so it runs strictly after the keyframe stage
    has been offloaded.
    """

    NAME = "Wan 2.1 14B"
    QUANT = "6-bit SVDQuant"
    PRIORITY = 90
    WEIGHTS_GB = 13.0
    WORKING_GB = 3.0
    ENV_WEIGHTS = "BASSITO_WAN21_WEIGHTS"

    def load(self) -> None:
        if self.loaded:
            return
        self._require_weights()
        logger.info("Loading %s (%s) from %s", self.NAME, self.QUANT, self.weights_dir)
        raise AppleSiliconRequiredError(
            "Wan 2.1 SVDQuant loader is not wired yet — the 6-bit checkpoint "
            "and an MPS/MLX runtime are required. "
            "Wan21Backend.load is the integration point."
        )

    def animate(self, request: I2VRequest) -> Path:
        if not self.loaded:
            self.load()
        raise AppleSiliconRequiredError(
            "Wan 2.1 inference requires the loaded SVDQuant model. "
            "Wan21Backend.animate is the integration point."
        )


class Wan21SmallBackend(Wan21Backend):
    """Wan 2.1 1.3B — light fallback; weaker on complex spatial transforms."""

    NAME = "Wan 2.1 1.3B"
    QUANT = "SVDQuant"
    PRIORITY = 50
    WEIGHTS_GB = 3.0
    WORKING_GB = 2.0
    ENV_WEIGHTS = "BASSITO_WAN21_SMALL_WEIGHTS"


class LTXVideoBackend(BaseI2VBackend):
    """LTX-Video (LTX-2.3, Lightricks) — native Apple MLX.

    Built for speed on Apple Silicon: runs through the MLX framework rather
    than the PyTorch MPS abstraction, giving markedly lower sampling latency.
    Compact enough to leave the OS plenty of headroom.
    """

    NAME = "LTX-Video 2.3"
    QUANT = "MLX"
    PRIORITY = 80
    WEIGHTS_GB = 6.0
    WORKING_GB = 2.0
    ENV_WEIGHTS = "BASSITO_LTXVIDEO_WEIGHTS"

    def load(self) -> None:
        if self.loaded:
            return
        self._require_weights()
        logger.info("Loading %s via Apple MLX from %s", self.NAME, self.weights_dir)
        raise AppleSiliconRequiredError(
            "LTX-Video MLX loader is not wired yet — the LTX-2.3 weights and "
            "the mlx runtime are required. LTXVideoBackend.load is the "
            "integration point."
        )

    def animate(self, request: I2VRequest) -> Path:
        if not self.loaded:
            self.load()
        raise AppleSiliconRequiredError(
            "LTX-Video inference requires the loaded MLX model. "
            "LTXVideoBackend.animate is the integration point."
        )


class HunyuanVideo15Backend(BaseI2VBackend):
    """HunyuanVideo 1.5 (Tencent) via the Wan2GP / LightX2V low-VRAM framework.

    Cinematic motion and realistic particle physics (fire / water / smoke).
    Wan2GP (v9.62+) drops the entry threshold to ~6 GB, so on 24 GB it runs
    with ample headroom for 720p context windows.
    """

    NAME = "HunyuanVideo 1.5"
    QUANT = "Wan2GP / LightX2V"
    PRIORITY = 85
    WEIGHTS_GB = 6.0
    WORKING_GB = 2.0
    ENV_WEIGHTS = "BASSITO_HUNYUAN15_WEIGHTS"

    def load(self) -> None:
        if self.loaded:
            return
        self._require_weights()
        logger.info("Loading %s via Wan2GP from %s", self.NAME, self.weights_dir)
        raise AppleSiliconRequiredError(
            "HunyuanVideo 1.5 loader is not wired yet — the checkpoint and the "
            "Wan2GP/LightX2V runtime are required. "
            "HunyuanVideo15Backend.load is the integration point."
        )

    def animate(self, request: I2VRequest) -> Path:
        if not self.loaded:
            self.load()
        raise AppleSiliconRequiredError(
            "HunyuanVideo 1.5 inference requires the loaded model. "
            "HunyuanVideo15Backend.animate is the integration point."
        )


# Registry in descending preference; `select_backend` walks it.
BACKENDS: tuple[type[BaseI2VBackend], ...] = (
    Wan21Backend,
    HunyuanVideo15Backend,
    LTXVideoBackend,
    Wan21SmallBackend,
)

_BY_NAME = {cls.NAME: cls for cls in BACKENDS}


def _weights_dir_for(cls: type[BaseI2VBackend]) -> Optional[Path]:
    val = os.getenv(cls.ENV_WEIGHTS)
    return Path(val) if val else None


def force_backend(name: str, weights_dir: Optional[Path] = None) -> BaseI2VBackend:
    """Instantiate a named backend explicitly (no memory check)."""
    cls = _BY_NAME.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown I2V backend {name!r}. Available: {sorted(_BY_NAME)}"
        )
    return cls(weights_dir or _weights_dir_for(cls))


def select_backend(
    budget: Optional[MemoryBudget] = None,
    prefer: Optional[str] = None,
) -> BaseI2VBackend:
    """
    Pick the best I2V backend that fits the memory budget.

    If `prefer` is given and it fits, it wins. Otherwise the highest-priority
    backend whose footprint fits the usable pool is chosen. Raises if nothing
    fits (the budget is too small even for the lightest backend).
    """
    budget = budget or MemoryBudget.from_env()
    if prefer is not None:
        forced = force_backend(prefer)
        if not forced.fits(budget):
            raise AppleSiliconRequiredError(
                f"Preferred backend {prefer!r} needs "
                f"~{forced.footprint.resident_gb:.1f} GB but only "
                f"{budget.usable_gb:.1f} GB is usable."
            )
        logger.info("Forced I2V backend: %s", forced.NAME)
        return forced

    candidates = sorted(BACKENDS, key=lambda c: c.PRIORITY, reverse=True)
    for cls in candidates:
        backend = cls(_weights_dir_for(cls))
        if backend.fits(budget):
            logger.info(
                "Selected I2V backend: %s (~%.1f GB resident, %.1f GB pool)",
                backend.NAME, backend.footprint.resident_gb, budget.usable_gb,
            )
            return backend

    lightest = min(BACKENDS, key=lambda c: c.WEIGHTS_GB + c.WORKING_GB)
    raise AppleSiliconRequiredError(
        f"No I2V backend fits the {budget.usable_gb:.1f} GB usable pool; the "
        f"lightest ({lightest.NAME}) needs "
        f"~{lightest.WEIGHTS_GB + lightest.WORKING_GB:.1f} GB."
    )
