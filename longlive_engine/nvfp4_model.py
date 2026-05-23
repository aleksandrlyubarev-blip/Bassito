"""
NVFP4 model wrapper for LongLive-2.0.

Hardware requirement: NVIDIA Blackwell (compute capability >= 10.0).
NVFP4 is the Blackwell-only 4-bit float datatype; it is not supported on
Hopper, Ada, Ampere, or older.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("bassito.longlive.nvfp4")

# sm_100 = B100/B200 datacenter, sm_120 = consumer RTX 50.
BLACKWELL_MIN_CAPABILITY = (10, 0)


class BlackwellRequiredError(RuntimeError):
    """Raised when NVFP4 inference is attempted on incompatible hardware
    or when the upstream LongLive-2.0 weights are not configured."""


def ensure_blackwell() -> tuple[int, int]:
    """
    Verify a Blackwell GPU is available. Returns the (major, minor)
    compute capability of GPU 0, or raises BlackwellRequiredError with
    a clear, actionable message.
    """
    try:
        import torch
    except ImportError as exc:
        raise BlackwellRequiredError(
            "torch is required for LongLive-2.0 inference. "
            "Install with: pip install 'torch>=2.6'"
        ) from exc

    if not torch.cuda.is_available():
        raise BlackwellRequiredError(
            "CUDA is not available. LongLive-2.0 NVFP4 inference requires "
            "an NVIDIA Blackwell GPU (B100/B200 or RTX 50-series)."
        )

    capability = torch.cuda.get_device_capability(0)
    if capability < BLACKWELL_MIN_CAPABILITY:
        name = torch.cuda.get_device_name(0)
        raise BlackwellRequiredError(
            f"NVFP4 requires Blackwell (compute capability >= "
            f"{BLACKWELL_MIN_CAPABILITY[0]}.{BLACKWELL_MIN_CAPABILITY[1]}). "
            f"GPU 0 is '{name}' with compute capability "
            f"{capability[0]}.{capability[1]}, which does not support NVFP4."
        )

    if torch.cuda.device_count() < 2:
        logger.warning(
            "Only 1 CUDA device visible. LongLive-2.0's best throughput "
            "uses 2 GPUs (model on GPU 0, async VAE decode on GPU 1). "
            "VAE decode will fall back to GPU 0 and the paper's 1.5x speedup "
            "will be reduced."
        )

    return capability


@dataclass(slots=True)
class LongLiveConfig:
    """Runtime configuration for the LongLive-2.0 engine."""

    weights_dir: Path
    chunk_size_frames: int = 16    # frames produced per autoregressive chunk
    kv_cache_chunks: int = 5       # sliding NVFP4 KV window (paper diagram: 5 chunks)
    vae_device_index: int = 1      # second GPU for async VAE decode
    model_device_index: int = 0
    fps: int = 24

    @classmethod
    def from_env(cls) -> "LongLiveConfig":
        weights = os.getenv("BASSITO_LONGLIVE_WEIGHTS")
        if not weights:
            raise BlackwellRequiredError(
                "BASSITO_LONGLIVE_WEIGHTS env var not set — point it at the "
                "directory containing the NVFP4 LongLive-2.0 weights."
            )
        return cls(
            weights_dir=Path(weights),
            chunk_size_frames=int(os.getenv("BASSITO_LONGLIVE_CHUNK_SIZE", "16")),
            kv_cache_chunks=int(os.getenv("BASSITO_LONGLIVE_KV_CHUNKS", "5")),
            vae_device_index=int(os.getenv("BASSITO_LONGLIVE_VAE_GPU", "1")),
            model_device_index=int(os.getenv("BASSITO_LONGLIVE_MODEL_GPU", "0")),
            fps=int(os.getenv("BASSITO_LONGLIVE_FPS", "24")),
        )


class LongLiveEngine:
    """
    Lazy-loading wrapper for the NVIDIA LongLive-2.0 NVFP4 model.

    Singleton-style: load once per process, then reuse for every job
    pulled off the FastAPI service or Bassito's job queue.
    """

    _instance: Optional["LongLiveEngine"] = None

    def __init__(self, config: Optional[LongLiveConfig] = None):
        ensure_blackwell()
        self.config = config or LongLiveConfig.from_env()
        self._model = None     # NVFP4 transformer (GPU 0)
        self._vae = None       # diffusion VAE (GPU 1)

    @classmethod
    def get(cls, config: Optional[LongLiveConfig] = None) -> "LongLiveEngine":
        if cls._instance is None:
            cls._instance = cls(config)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Test helper: drop the singleton so the next get() reloads."""
        cls._instance = None

    @property
    def model(self) -> object:
        return self._model

    @property
    def vae(self) -> object:
        return self._vae

    @property
    def loaded(self) -> bool:
        return self._model is not None and self._vae is not None

    def load(self) -> None:
        """
        Load NVFP4 weights onto the configured GPUs.

        Integration point for the upstream NVIDIA LongLive-2.0 release.
        Expected shape once the public weights ship:

            from longlive import LongLiveAR, LongLiveVAE
            self._model = LongLiveAR.from_pretrained(
                self.config.weights_dir,
                dtype="nvfp4",
                device=f"cuda:{self.config.model_device_index}",
            )
            self._vae = LongLiveVAE.from_pretrained(
                self.config.weights_dir / "vae",
                device=f"cuda:{self.config.vae_device_index}",
            )
        """
        if self.loaded:
            return
        if not self.config.weights_dir.exists():
            raise BlackwellRequiredError(
                f"LongLive-2.0 weights not found at {self.config.weights_dir}. "
                "Set BASSITO_LONGLIVE_WEIGHTS to the unpacked NVFP4 checkpoint."
            )
        logger.info(
            "Loading LongLive-2.0 NVFP4 weights from %s "
            "(model->cuda:%d, vae->cuda:%d, kv window=%d chunks)",
            self.config.weights_dir,
            self.config.model_device_index,
            self.config.vae_device_index,
            self.config.kv_cache_chunks,
        )
        raise BlackwellRequiredError(
            "LongLive-2.0 model loader is not wired yet — upstream NVIDIA "
            "release is required. See longlive_engine/nvfp4_model.py:LongLiveEngine.load "
            "for the integration point."
        )
