"""
Ideogram 4 text-to-image keyframe generator (Apple Silicon, quantized).

Ideogram 4 (released open-weights, 2026-06-03) is a from-scratch single-stream
Diffusion Transformer: 9.3B params / 34 layers, conditioned on hidden states
pulled from 13 intermediate layers of a Qwen3-VL-8B-Instruct text encoder. It
was trained on highly structured JSON annotations, so it accepts deterministic
layout control — bounding boxes, HEX colors and multi-font typography — and
renders text with near-perfect OCR fidelity.

In the local I2V pipeline its job is to produce the *perfect first frame*. I2V
models are extremely sensitive to keyframe quality: any anatomical / perspective
/ typographic defect in the start frame is extrapolated through time as if it
were real geometry ("AI slop"). A clean Ideogram 4 keyframe acts as a quality
multiplier for whatever I2V backend follows it.

Fitting 9.3B into 24 GB requires quantization. Ideogram AI ships two
checkpoints: ``ideogram-4-fp8`` and ``ideogram-4-nf4``. On M5 Pro 24 GB the
recommended choice is **nf4** (~6-8 GB resident for the DiT), leaving room for
the encoder and latent math.

The JSON-prompt builder and config here are pure and fully testable; the actual
weight load / sampling is the single gated integration point.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .unified_memory import (
    AppleSiliconRequiredError,
    ModelFootprint,
    ensure_apple_silicon,
)

logger = logging.getLogger("bassito.m5.ideogram4")

# License note: weights are published under "Ideogram 4 Non-Commercial".
# Locally generated output may not be used for commercial gain.
LICENSE = "Ideogram 4 Non-Commercial"

# nf4 is the recommended 24 GB quant; fp8 is sharper but tighter on memory.
_QUANT_WEIGHTS_GB = {
    "nf4": 7.0,   # ~6-8 GB resident for the 9.3B DiT at 4-bit
    "fp8": 11.0,  # ~10-12 GB resident at 8-bit
}
_VALID_QUANTS = tuple(_QUANT_WEIGHTS_GB)


@dataclass(slots=True, frozen=True)
class BoundingBox:
    """A layout box in Ideogram's normalized [y_min, x_min, y_max, x_max] order.

    Coordinates are 0..1. The unusual y-first ordering matches the format the
    model was trained on; `as_list` emits it verbatim.
    """

    y_min: float
    x_min: float
    y_max: float
    x_max: float

    def __post_init__(self) -> None:
        for name in ("y_min", "x_min", "y_max", "x_max"):
            v = getattr(self, name)
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"BoundingBox.{name}={v} out of range [0, 1]")
        if self.y_max <= self.y_min or self.x_max <= self.x_min:
            raise ValueError("BoundingBox max coords must exceed min coords")

    def as_list(self) -> list[float]:
        return [self.y_min, self.x_min, self.y_max, self.x_max]


@dataclass(slots=True)
class TextElement:
    """One typographic element to render into the composition."""

    text: str
    box: Optional[BoundingBox] = None
    hex_color: Optional[str] = None
    font: Optional[str] = None

    def to_json(self) -> dict:
        out: dict = {"text": self.text}
        if self.box is not None:
            out["bbox"] = self.box.as_list()
        if self.hex_color is not None:
            out["color"] = _normalize_hex(self.hex_color)
        if self.font is not None:
            out["font"] = self.font
        return out


@dataclass(slots=True)
class SceneObject:
    """A non-text object placed via a bounding box, optionally color-keyed."""

    description: str
    box: Optional[BoundingBox] = None
    hex_color: Optional[str] = None

    def to_json(self) -> dict:
        out: dict = {"description": self.description}
        if self.box is not None:
            out["bbox"] = self.box.as_list()
        if self.hex_color is not None:
            out["color"] = _normalize_hex(self.hex_color)
        return out


@dataclass(slots=True)
class JSONPrompt:
    """
    Structured JSON prompt — the native conditioning format for Ideogram 4.

    Build it explicitly for deterministic control, or via `from_text` for a
    plain caption. `render` produces the JSON string the model consumes.
    """

    scene: str
    objects: list[SceneObject] = field(default_factory=list)
    text_elements: list[TextElement] = field(default_factory=list)
    style: Optional[str] = None
    background_hex: Optional[str] = None

    @classmethod
    def from_text(cls, prompt: str) -> "JSONPrompt":
        return cls(scene=prompt)

    def to_dict(self) -> dict:
        out: dict = {"scene": self.scene}
        if self.style:
            out["style"] = self.style
        if self.background_hex:
            out["background_color"] = _normalize_hex(self.background_hex)
        if self.objects:
            out["objects"] = [o.to_json() for o in self.objects]
        if self.text_elements:
            out["text"] = [t.to_json() for t in self.text_elements]
        return out

    def render(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


def _normalize_hex(value: str) -> str:
    v = value.strip().lstrip("#").upper()
    if len(v) not in (3, 6) or any(c not in "0123456789ABCDEF" for c in v):
        raise ValueError(f"Invalid HEX color: {value!r}")
    return "#" + v


@dataclass(slots=True)
class Ideogram4Config:
    """Runtime configuration for the Ideogram 4 keyframe generator."""

    weights_dir: Path
    quant: str = "nf4"
    width: int = 1024
    height: int = 1024
    # Asymmetric CFG: the unconditional pass drops text tokens entirely, which
    # speeds up sampling. This is the guidance scale for the conditional pass.
    guidance_scale: float = 5.0
    steps: int = 28
    seed: Optional[int] = None

    def __post_init__(self) -> None:
        if self.quant not in _VALID_QUANTS:
            raise ValueError(
                f"quant must be one of {_VALID_QUANTS}, got {self.quant!r}"
            )
        # Native resolution scales up to 2K without LoRAs.
        if max(self.width, self.height) > 2048:
            raise ValueError("Ideogram 4 native resolution caps at 2K (2048 px)")

    @property
    def footprint(self) -> ModelFootprint:
        # Working set also has to hold the Qwen3-VL-8B encoder activations.
        return ModelFootprint(
            name="Ideogram 4",
            quant=self.quant,
            weights_gb=_QUANT_WEIGHTS_GB[self.quant],
            working_gb=3.0,
        )

    @classmethod
    def from_env(cls) -> "Ideogram4Config":
        weights = os.getenv("BASSITO_IDEOGRAM4_WEIGHTS")
        if not weights:
            raise AppleSiliconRequiredError(
                "BASSITO_IDEOGRAM4_WEIGHTS env var not set — point it at the "
                "unpacked Ideogram 4 checkpoint (nf4 recommended for 24 GB)."
            )
        return cls(
            weights_dir=Path(weights),
            quant=os.getenv("BASSITO_IDEOGRAM4_QUANT", "nf4"),
            width=int(os.getenv("BASSITO_IDEOGRAM4_WIDTH", "1024")),
            height=int(os.getenv("BASSITO_IDEOGRAM4_HEIGHT", "1024")),
            guidance_scale=float(os.getenv("BASSITO_IDEOGRAM4_CFG", "5.0")),
            steps=int(os.getenv("BASSITO_IDEOGRAM4_STEPS", "28")),
        )


class Ideogram4KeyframeGenerator:
    """
    Lazy-loading Ideogram 4 T2I generator for first-frame keyframes.

    Singleton-style: load the nf4 weights once, reuse across jobs. After a
    keyframe is produced the caller is expected to `unload()` before the I2V
    backend loads, because the two cannot be co-resident in 24 GB.
    """

    _instance: Optional["Ideogram4KeyframeGenerator"] = None

    def __init__(self, config: Optional[Ideogram4Config] = None):
        self.config = config or Ideogram4Config.from_env()
        self._model = None    # DiT (9.3B)
        self._encoder = None  # Qwen3-VL-8B-Instruct text encoder

    @classmethod
    def get(cls, config: Optional[Ideogram4Config] = None) -> "Ideogram4KeyframeGenerator":
        if cls._instance is None:
            cls._instance = cls(config)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Test helper: drop the singleton so the next get() reloads."""
        cls._instance = None

    @property
    def loaded(self) -> bool:
        return self._model is not None and self._encoder is not None

    @property
    def footprint(self) -> ModelFootprint:
        return self.config.footprint

    def load(self) -> None:
        """
        Load the nf4 DiT and the Qwen3-VL encoder via MLX / MPS.

        Integration point for the Ideogram 4 release. Expected shape:

            import mlx.core as mx
            from ideogram4_mlx import Ideogram4DiT, Qwen3VLEncoder
            self._encoder = Qwen3VLEncoder.from_pretrained(self.config.weights_dir / "text_encoder")
            self._model = Ideogram4DiT.from_pretrained(
                self.config.weights_dir, quant=self.config.quant
            )
        """
        if self.loaded:
            return
        ensure_apple_silicon()
        if not self.config.weights_dir.exists():
            raise AppleSiliconRequiredError(
                f"Ideogram 4 weights not found at {self.config.weights_dir}. "
                "Set BASSITO_IDEOGRAM4_WEIGHTS to the unpacked nf4 checkpoint."
            )
        logger.info(
            "Loading Ideogram 4 (%s) from %s — DiT 9.3B + Qwen3-VL-8B encoder",
            self.config.quant, self.config.weights_dir,
        )
        raise AppleSiliconRequiredError(
            "Ideogram 4 MLX loader is not wired yet — the open-weights "
            "checkpoint + an MLX/MPS sampler are required. See "
            "m5_video_engine/ideogram4.py:Ideogram4KeyframeGenerator.load "
            "for the integration point."
        )

    def unload(self) -> None:
        """
        Free the DiT + encoder so the I2V backend can claim the unified pool.

        Drops references and lets the MLX allocator reclaim the memory; on Mac
        this is what makes strictly-sequential offload viable in 24 GB.
        """
        self._model = None
        self._encoder = None
        logger.info("Ideogram 4 unloaded — unified-memory pool released for I2V stage")

    def generate(self, prompt: JSONPrompt | str, out_path: Path) -> Path:
        """
        Sample one keyframe and write it to `out_path`. Returns `out_path`.

        Accepts either a raw caption or a structured JSONPrompt; a raw caption
        is wrapped via `JSONPrompt.from_text` so the model still receives its
        native JSON conditioning.
        """
        if not self.loaded:
            self.load()
        json_prompt = (
            prompt if isinstance(prompt, JSONPrompt) else JSONPrompt.from_text(prompt)
        )
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Sampling Ideogram 4 keyframe %dx%d (cfg=%.1f, steps=%d) -> %s",
            self.config.width, self.config.height,
            self.config.guidance_scale, self.config.steps, out_path,
        )
        _ = json_prompt.render()  # conditioning payload handed to the sampler
        raise AppleSiliconRequiredError(
            "Ideogram 4 sampling requires the loaded MLX model. "
            "Ideogram4KeyframeGenerator.generate is the integration point."
        )
