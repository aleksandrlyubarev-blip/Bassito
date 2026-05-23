"""
LongLive-2.0 inference engine for Bassito.

Wraps NVIDIA Research's LongLive-2.0 — an autoregressive, multi-shot,
long-video diffusion model that runs in NVFP4 on Blackwell GPUs with
a sliding KV cache window, parallel dequantization, and async VAE
decode pinned to a second GPU.

Public surface:
    LongLiveEngine        — lazy-loading model wrapper (singleton)
    LongLiveConfig        — weights / GPU / chunking configuration
    BlackwellRequiredError— raised when hardware/weights are missing
    ensure_blackwell()    — hardware capability check
    ShotSpec              — one shot in a multi-shot storyboard
    GenerationResult      — result of a multi-shot generation run
    ChunkEvent            — streaming event emitted per decoded chunk
    MultiShotRunner       — drives the engine across a shot list
    SlidingKVCache        — bounded NVFP4 KV window (paper diagram: 5 chunks)
    AsyncVAEDecoder       — async VAE pump on a second GPU

The NVFP4 inference itself requires a Blackwell GPU and the upstream
NVIDIA LongLive-2.0 weights. The wrapper raises clear errors when
either is missing; the surrounding plumbing (FastAPI service,
bassito_core integration, PinoCut bridge, romeo_phd control plane)
is fully exercisable against the stable types defined here.
"""
from .nvfp4_model import (
    BlackwellRequiredError,
    LongLiveConfig,
    LongLiveEngine,
    ensure_blackwell,
)
from .kv_cache_window import KVChunk, SlidingKVCache
from .async_vae_decode import AsyncVAEDecoder, FrameChunk, LatentChunk
from .multi_shot_runner import (
    ChunkEvent,
    GenerationResult,
    MultiShotRunner,
    ShotSpec,
)

__all__ = [
    "AsyncVAEDecoder",
    "BlackwellRequiredError",
    "ChunkEvent",
    "FrameChunk",
    "GenerationResult",
    "KVChunk",
    "LatentChunk",
    "LongLiveConfig",
    "LongLiveEngine",
    "MultiShotRunner",
    "ShotSpec",
    "SlidingKVCache",
    "ensure_blackwell",
]
