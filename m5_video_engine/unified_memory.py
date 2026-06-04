"""
Unified Memory Architecture (UMA) budgeting for Apple Silicon.

The defining constraint of local image-to-video on a MacBook Pro M5 Pro is
not raw TFLOPs but the 24 GB unified memory pool. macOS, the window server
and the host app (ComfyUI / Draw Things / a Python process) reserve 4-6 GB,
leaving ~18-20 GB for model weights, text encoders, the VAE and the working
latents / KV cache.

A heavy T2I model (Ideogram 4, 9.3B) and a heavy I2V model (e.g. Wan 2.1 14B)
cannot be resident simultaneously inside ~18 GB. This module encodes that
reality: it sizes the usable pool, decides whether a set of model footprints
fits, and — when they do not — flags that the pipeline must run strictly
sequentially with aggressive offloading between stages.

The memory math here is pure and fully testable; only `ensure_apple_silicon`
touches the host platform.
"""
from __future__ import annotations

import logging
import os
import platform
from dataclasses import dataclass, field

logger = logging.getLogger("bassito.m5.memory")

# M5 Pro unified-memory bandwidth (datasheet figure). Quoted by callers that
# reason about Time-to-First-Token / sampling throughput being bandwidth-bound.
M5_PRO_MEMORY_BANDWIDTH_GBPS = 307.0


class AppleSiliconRequiredError(RuntimeError):
    """Raised when the local MLX / Metal pipeline is attempted on a host that
    is not Apple Silicon, or when required weights are not configured."""


def ensure_apple_silicon() -> str:
    """
    Verify the host is an Apple Silicon Mac. Returns a short platform
    descriptor (e.g. ``"Darwin arm64"``) or raises AppleSiliconRequiredError
    with an actionable message.

    The MLX / MPS backends that LTX-Video, Wan 2.1 and HunyuanVideo use on Mac
    only exist on macOS arm64; on any other host the local pipeline cannot run
    (callers should fall back to a cloud or Blackwell path instead).
    """
    system = platform.system()
    machine = platform.machine().lower()
    if system != "Darwin" or machine not in ("arm64", "aarch64"):
        raise AppleSiliconRequiredError(
            "Local M5 image-to-video requires an Apple Silicon Mac "
            "(macOS arm64). Detected "
            f"'{system} {machine or 'unknown'}'. Use the cloud / Blackwell "
            "path on this host instead."
        )
    return f"{system} {machine}"


@dataclass(slots=True, frozen=True)
class ModelFootprint:
    """Approximate resident memory of one model stage.

    `weights_gb` is the quantized weight size; `working_gb` covers the
    transient latents / KV cache / activation scratch the stage needs on top
    of its weights while it runs.
    """

    name: str
    quant: str
    weights_gb: float
    working_gb: float = 2.0

    @property
    def resident_gb(self) -> float:
        return self.weights_gb + self.working_gb


@dataclass(slots=True)
class MemoryBudget:
    """
    Unified-memory budget for a single Apple Silicon configuration.

    Defaults model the M5 Pro 24 GB part: ~5 GB reserved by the OS + host app,
    leaving ~19 GB usable for weights + working set.
    """

    total_gb: float = 24.0
    os_reserved_gb: float = 5.0
    bandwidth_gbps: float = M5_PRO_MEMORY_BANDWIDTH_GBPS

    @property
    def usable_gb(self) -> float:
        """Pool available for model weights + working tensors."""
        return max(0.0, self.total_gb - self.os_reserved_gb)

    def fits(self, *footprints: ModelFootprint) -> bool:
        """True if all given stages can be co-resident inside the usable pool."""
        return self.required_gb(*footprints) <= self.usable_gb

    def required_gb(self, *footprints: ModelFootprint) -> float:
        """Total resident memory if all stages were loaded at once."""
        return sum(fp.resident_gb for fp in footprints)

    def must_offload(self, *footprints: ModelFootprint) -> bool:
        """
        True if the stages cannot co-exist and must therefore run strictly
        sequentially, freeing each stage before loading the next.
        """
        return not self.fits(*footprints)

    def headroom_gb(self, *footprints: ModelFootprint) -> float:
        """Free pool remaining with the given stages resident (may be negative)."""
        return self.usable_gb - self.required_gb(*footprints)

    @classmethod
    def from_env(cls) -> "MemoryBudget":
        return cls(
            total_gb=float(os.getenv("BASSITO_M5_TOTAL_GB", "24")),
            os_reserved_gb=float(os.getenv("BASSITO_M5_OS_RESERVED_GB", "5")),
        )


@dataclass(slots=True)
class OffloadPlan:
    """
    The result of planning a multi-stage pipeline against a memory budget.

    `sequential` is True when stages must be loaded/unloaded one at a time;
    `stages` is the ordered list of footprints; `notes` carries human-readable
    reasoning for logs / Telegram status.
    """

    budget: MemoryBudget
    stages: list[ModelFootprint]
    sequential: bool
    notes: list[str] = field(default_factory=list)

    @classmethod
    def plan(cls, budget: MemoryBudget, stages: list[ModelFootprint]) -> "OffloadPlan":
        notes: list[str] = []
        for fp in stages:
            fit = "fits" if fp.resident_gb <= budget.usable_gb else "TOO LARGE"
            notes.append(
                f"{fp.name} [{fp.quant}] ~{fp.resident_gb:.1f} GB resident "
                f"({fit} in {budget.usable_gb:.1f} GB pool)"
            )
        sequential = budget.must_offload(*stages)
        if sequential:
            notes.append(
                f"Co-resident total ~{budget.required_gb(*stages):.1f} GB exceeds "
                f"{budget.usable_gb:.1f} GB pool — stages run sequentially with offload."
            )
        else:
            notes.append(
                f"All stages co-resident (~{budget.required_gb(*stages):.1f} GB) "
                f"fit the {budget.usable_gb:.1f} GB pool."
            )
        oversized = [fp for fp in stages if fp.resident_gb > budget.usable_gb]
        if oversized:
            names = ", ".join(fp.name for fp in oversized)
            raise AppleSiliconRequiredError(
                f"Stage(s) {names} do not fit the {budget.usable_gb:.1f} GB usable "
                "pool even in isolation. Use a smaller quant (e.g. nf4 / 6-bit "
                "SVDQuant) or a lighter model."
            )
        return cls(budget=budget, stages=list(stages), sequential=sequential, notes=notes)
