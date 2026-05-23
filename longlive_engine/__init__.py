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
    MockLongLiveEngine    — CPU/Cloud-Run fallback for UX testing
    MockMultiShotRunner   — synthetic ChunkEvent emitter for plumbing tests
    make_engine()         — factory honoring BASSITO_LONGLIVE_MOCK
    make_runner()         — factory honoring BASSITO_LONGLIVE_MOCK
    is_mock_enabled()     — truthy when BASSITO_LONGLIVE_MOCK is set

The NVFP4 inference itself requires a Blackwell GPU and the upstream
NVIDIA LongLive-2.0 weights. The wrapper raises clear errors when
either is missing; the mock backend lets the surrounding plumbing
(FastAPI service, bassito_core integration, PinoCut bridge, romeo_phd
control plane) be exercised on free-tier infrastructure.
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
from .mock_engine import (
    MockLongLiveEngine,
    MockMultiShotRunner,
    is_mock_enabled,
    make_engine,
    make_runner,
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
    "MockLongLiveEngine",
    "MockMultiShotRunner",
    "MultiShotRunner",
    "ShotSpec",
    "SlidingKVCache",
    "ensure_blackwell",
    "is_mock_enabled",
    "make_engine",
    "make_runner",
]
