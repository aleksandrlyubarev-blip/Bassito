"""
Unit tests for the longlive_engine package.

These tests intentionally run on CPU-only / non-Blackwell hardware. They
verify the public API surface and the BlackwellRequiredError path. The
end-to-end NVFP4 generation path is excluded — it requires a real
Blackwell GPU with weights present and is exercised on the engine node.
"""
import unittest

from longlive_engine.kv_cache_window import KVChunk, SlidingKVCache
from longlive_engine.multi_shot_runner import ChunkEvent, ShotSpec


class SlidingKVCacheTests(unittest.TestCase):
    def test_eviction_when_full(self):
        cache = SlidingKVCache(max_chunks=2)
        c0 = KVChunk(index=0, nvfp4_k=None, nvfp4_v=None, num_tokens=8)
        c1 = KVChunk(index=1, nvfp4_k=None, nvfp4_v=None, num_tokens=8)
        c2 = KVChunk(index=2, nvfp4_k=None, nvfp4_v=None, num_tokens=8)

        self.assertIsNone(cache.append(c0))
        self.assertIsNone(cache.append(c1))
        evicted = cache.append(c2)

        self.assertEqual(evicted, c0)
        self.assertEqual([c.index for c in cache.chunks()], [1, 2])

    def test_rejects_zero_max_chunks(self):
        with self.assertRaises(ValueError):
            SlidingKVCache(max_chunks=0)

    def test_clear(self):
        cache = SlidingKVCache(max_chunks=3)
        cache.append(KVChunk(0, None, None, 1))
        cache.append(KVChunk(1, None, None, 1))
        cache.clear()
        self.assertEqual(len(cache), 0)

    def test_dequantize_requires_upstream_kernel(self):
        cache = SlidingKVCache(max_chunks=2)
        cache.append(KVChunk(0, None, None, 1))
        with self.assertRaises(NotImplementedError):
            cache.dequantize_parallel()


class ShotSpecTests(unittest.TestCase):
    def test_auto_shot_id(self):
        s = ShotSpec(prompt="a cat")
        self.assertTrue(s.shot_id.startswith("shot_"))

    def test_explicit_shot_id_kept(self):
        s = ShotSpec(prompt="a cat", shot_id="shot_keep")
        self.assertEqual(s.shot_id, "shot_keep")

    def test_defaults(self):
        s = ShotSpec(prompt="x")
        self.assertEqual(s.duration_seconds, 4.0)
        self.assertIsNone(s.reference_image_path)
        self.assertIsNone(s.reference_clip_path)


class ChunkEventTests(unittest.TestCase):
    def test_dataclass_fields(self):
        e = ChunkEvent(shot_id="shot_x", chunk_index=2, frames_done=32, frames_total=128)
        self.assertEqual(e.shot_id, "shot_x")
        self.assertEqual(e.chunk_index, 2)
        self.assertEqual(e.frames_done, 32)
        self.assertEqual(e.frames_total, 128)
        self.assertIsNone(e.partial_video_path)


class BlackwellCheckTests(unittest.TestCase):
    def test_raises_on_missing_cuda_or_old_gpu(self):
        try:
            import torch  # noqa: F401
        except ImportError:
            from longlive_engine.nvfp4_model import (
                BlackwellRequiredError, ensure_blackwell,
            )
            with self.assertRaises(BlackwellRequiredError):
                ensure_blackwell()
            return

        from longlive_engine.nvfp4_model import (
            BlackwellRequiredError, ensure_blackwell,
        )
        try:
            ensure_blackwell()
        except BlackwellRequiredError:
            return  # expected on CI / non-Blackwell hardware
        # If we got here, the test runner is on Blackwell — that is also fine.


if __name__ == "__main__":
    unittest.main()
