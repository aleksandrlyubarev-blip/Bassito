"""
Local image-to-video engine for MacBook Pro M5 Pro (24 GB unified memory).

A two-stage, strictly-sequential pipeline tuned for Apple Silicon's Unified
Memory Architecture: Ideogram 4 (nf4) renders a perfect first frame, the
keyframe model is offloaded to free the ~18-20 GB usable pool, then a quantized
I2V backend (Wan 2.1 / LTX-Video / HunyuanVideo 1.5) animates it.

Public surface:
    AppleSiliconRequiredError — raised when host/weights are not Apple Silicon ready
    ensure_apple_silicon()    — host capability check (macOS arm64)
    MemoryBudget              — 24 GB unified-memory budgeting
    ModelFootprint            — per-stage resident-memory estimate
    OffloadPlan               — sequential-vs-coresident plan for a budget

    Ideogram4Config           — keyframe generator configuration
    Ideogram4KeyframeGenerator— lazy-loading nf4 T2I first-frame generator
    JSONPrompt / BoundingBox / TextElement / SceneObject — structured prompting

    BaseI2VBackend            — ABC for I2V backends
    Wan21Backend / Wan21SmallBackend / LTXVideoBackend / HunyuanVideo15Backend
    select_backend / force_backend — memory-aware backend factory
    I2VRequest                — one image-to-video job

    M5VideoConfig / M5VideoPipeline / I2VResult — the end-to-end orchestrator

The model load / sampling calls require the open-weights checkpoints and an
MLX/MPS (or Wan2GP) runtime on a real Mac; the wrapper raises clear errors when
either is missing, while every surrounding piece — memory planning, JSON-prompt
building, backend selection, offload sequencing — is exercisable here.
"""
from .unified_memory import (
    AppleSiliconRequiredError,
    MemoryBudget,
    ModelFootprint,
    OffloadPlan,
    M5_PRO_MEMORY_BANDWIDTH_GBPS,
    ensure_apple_silicon,
)
from .ideogram4 import (
    BoundingBox,
    Ideogram4Config,
    Ideogram4KeyframeGenerator,
    JSONPrompt,
    SceneObject,
    TextElement,
)
from .i2v_models import (
    BaseI2VBackend,
    HunyuanVideo15Backend,
    I2VRequest,
    LTXVideoBackend,
    Wan21Backend,
    Wan21SmallBackend,
    force_backend,
    select_backend,
)
from .i2v_pipeline import (
    I2VResult,
    M5VideoConfig,
    M5VideoPipeline,
)

__all__ = [
    # unified_memory
    "AppleSiliconRequiredError",
    "MemoryBudget",
    "ModelFootprint",
    "OffloadPlan",
    "M5_PRO_MEMORY_BANDWIDTH_GBPS",
    "ensure_apple_silicon",
    # ideogram4
    "BoundingBox",
    "Ideogram4Config",
    "Ideogram4KeyframeGenerator",
    "JSONPrompt",
    "SceneObject",
    "TextElement",
    # i2v_models
    "BaseI2VBackend",
    "HunyuanVideo15Backend",
    "I2VRequest",
    "LTXVideoBackend",
    "Wan21Backend",
    "Wan21SmallBackend",
    "force_backend",
    "select_backend",
    # i2v_pipeline
    "I2VResult",
    "M5VideoConfig",
    "M5VideoPipeline",
]
