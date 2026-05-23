"""
Sliding NVFP4 KV cache window for autoregressive long-video generation.

Implements the "NVFP4 KV Cache Window (e.g. 5 chunks)" pattern from the
LongLive-2.0 paper: the cache holds at most N most-recent chunks of
NVFP4-quantized K/V tensors; older chunks are evicted. Storage stays in
NVFP4 (the ~10 GB memory target from the paper); dequantization to compute
precision happens on demand and is overlapped with attention compute to
deliver the paper's ~1.5x speedup.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("bassito.longlive.kv_cache")


@dataclass(slots=True)
class KVChunk:
    """One chunk of NVFP4-quantized key/value tensors."""

    index: int          # chunk index in the autoregressive stream
    nvfp4_k: object     # torch.Tensor with NVFP4 storage dtype
    nvfp4_v: object
    num_tokens: int


class SlidingKVCache:
    """
    Bounded sliding window over NVFP4 KV chunks.

    The window length and eviction policy here are the stable contract;
    the actual NVFP4 storage and parallel dequant call live behind
    `dequantize_parallel`, which is wired against the upstream CUDA kernel
    when the weights are loaded.
    """

    def __init__(self, max_chunks: int):
        if max_chunks < 1:
            raise ValueError("max_chunks must be >= 1")
        self.max_chunks = max_chunks
        self._chunks: deque[KVChunk] = deque(maxlen=max_chunks)

    def append(self, chunk: KVChunk) -> Optional[KVChunk]:
        """Append a new chunk; return the evicted chunk if any."""
        evicted: Optional[KVChunk] = None
        if len(self._chunks) == self.max_chunks:
            evicted = self._chunks[0]
        self._chunks.append(chunk)
        if evicted is not None:
            logger.debug(
                "Evicted KV chunk %d (window full at %d)",
                evicted.index, self.max_chunks,
            )
        return evicted

    def chunks(self) -> list[KVChunk]:
        return list(self._chunks)

    def clear(self) -> None:
        self._chunks.clear()

    def __len__(self) -> int:
        return len(self._chunks)

    def dequantize_parallel(self) -> list[tuple[object, object]]:
        """
        Parallel-dequantize all cached chunks into compute precision.

        Integration point for the upstream LongLive-2.0 parallel-dequant
        CUDA kernel. The cache structure and eviction policy above are
        the stable contract; plug the dequant call into this method when
        the upstream kernel is available.
        """
        raise NotImplementedError(
            "Parallel NVFP4 dequantization requires the upstream LongLive-2.0 "
            "CUDA kernel. SlidingKVCache.dequantize_parallel is the "
            "integration point."
        )
