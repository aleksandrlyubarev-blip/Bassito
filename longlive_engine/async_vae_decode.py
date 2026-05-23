"""
Asynchronous VAE decode on a second GPU.

Per the LongLive-2.0 diagram: GPU 0 runs the NVFP4 diffusion transformer
and emits latents per chunk; GPU 1 runs the VAE and decodes those latents
into pixel frames asynchronously. The two pipelines overlap, so VAE
decode does not block the next chunk of denoising.

This module wires the async pump (queues + background task). The actual
VAE forward call is the only thing the upstream model needs to provide.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import AsyncIterator, Optional

logger = logging.getLogger("bassito.longlive.vae")


@dataclass(slots=True)
class LatentChunk:
    """One chunk of denoised latents waiting for VAE decode."""

    index: int
    latent: object        # torch.Tensor on the model GPU
    is_last: bool = False


@dataclass(slots=True)
class FrameChunk:
    """Decoded pixel frames for one chunk."""

    index: int
    frames: object        # torch.Tensor (T, C, H, W) on the VAE GPU
    is_last: bool = False


class AsyncVAEDecoder:
    """
    Pulls LatentChunks from a queue and emits FrameChunks.

    Designed to be driven by MultiShotRunner on a background task:
        decoder = AsyncVAEDecoder(vae, device_index=1)
        decoder.start()
        await decoder.submit(LatentChunk(...))
        async for frames in decoder.stream(): ...
        await decoder.close()
    """

    def __init__(self, vae: object, device_index: int = 1, queue_size: int = 4):
        self.vae = vae
        self.device_index = device_index
        self._inbox: asyncio.Queue[Optional[LatentChunk]] = asyncio.Queue(maxsize=queue_size)
        self._outbox: asyncio.Queue[FrameChunk] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="longlive-vae-decode")

    async def submit(self, chunk: LatentChunk) -> None:
        await self._inbox.put(chunk)

    async def close(self) -> None:
        await self._inbox.put(None)
        if self._task is not None:
            await self._task
            self._task = None

    async def stream(self) -> AsyncIterator[FrameChunk]:
        while True:
            chunk = await self._outbox.get()
            yield chunk
            if chunk.is_last:
                return

    async def _run(self) -> None:
        while True:
            chunk = await self._inbox.get()
            if chunk is None:
                return
            frames = await self._decode_one(chunk)
            await self._outbox.put(frames)
            if chunk.is_last:
                return

    async def _decode_one(self, chunk: LatentChunk) -> FrameChunk:
        """
        Decode a single latent chunk on the VAE GPU.

        Integration point for the upstream LongLive-2.0 VAE module.
        Replace with: `frames = await asyncio.to_thread(self.vae.decode, chunk.latent.to(f'cuda:{self.device_index}'))`
        """
        raise NotImplementedError(
            "VAE decode requires the upstream LongLive-2.0 VAE module. "
            "AsyncVAEDecoder._decode_one is the integration point."
        )
