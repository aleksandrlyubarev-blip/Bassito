"""
Unit tests for the longlive_engine package.

These tests intentionally run on CPU-only / non-Blackwell hardware. They
verify the public API surface, the BlackwellRequiredError path, and the
mock backend end-to-end (since the mock is exactly what runs on Cloud
Run / e2-micro during free-credit testing).
"""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from longlive_engine.kv_cache_window import KVChunk, SlidingKVCache
from longlive_engine.mock_engine import (
    MockLongLiveEngine,
    MockMultiShotRunner,
    is_mock_enabled,
    make_engine,
    make_runner,
)
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


class MockEngineTests(unittest.TestCase):
    def test_is_mock_enabled_truthy_values(self):
        for val in ("1", "true", "TRUE", "yes", "on"):
            with mock.patch.dict(os.environ, {"BASSITO_LONGLIVE_MOCK": val}):
                self.assertTrue(is_mock_enabled(), f"failed for {val!r}")

    def test_is_mock_enabled_falsy(self):
        with mock.patch.dict(os.environ, {"BASSITO_LONGLIVE_MOCK": "0"}):
            self.assertFalse(is_mock_enabled())
        with mock.patch.dict(os.environ, {"BASSITO_LONGLIVE_MOCK": ""}, clear=False):
            self.assertFalse(is_mock_enabled())

    def test_mock_engine_is_loaded_without_blackwell(self):
        engine = MockLongLiveEngine()
        self.assertTrue(engine.loaded)
        engine.load()  # idempotent + non-throwing
        self.assertTrue(engine.loaded)

    def test_make_engine_returns_mock_when_env_set(self):
        with mock.patch.dict(os.environ, {
            "BASSITO_LONGLIVE_MOCK": "1",
        }):
            engine = make_engine()
            self.assertIsInstance(engine, MockLongLiveEngine)

    def test_mock_runner_emits_synthetic_chunks(self):
        with mock.patch.dict(os.environ, {
            "BASSITO_LONGLIVE_MOCK": "1",
            "BASSITO_LONGLIVE_MOCK_CHUNK_MS": "0",
            "BASSITO_LONGLIVE_MOCK_CHUNKS_PER_SHOT": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                engine = make_engine()
                runner = make_runner(engine, Path(tmp))
                self.assertIsInstance(runner, MockMultiShotRunner)

                shots = [ShotSpec(prompt="a"), ShotSpec(prompt="b")]
                events: list[ChunkEvent] = []

                async def drive():
                    async for event in runner.stream("job_test", shots):
                        events.append(event)

                asyncio.run(drive())

                # 3 chunks per shot * 2 shots
                self.assertEqual(len(events), 6)
                self.assertEqual(events[-1].shot_id, shots[-1].shot_id)
                self.assertGreater(events[-1].frames_done, 0)

                # Placeholder mp4 files should exist for each shot + long video
                long_path = Path(tmp) / "job_test_long.mp4"
                self.assertTrue(long_path.exists())
                for shot in shots:
                    self.assertTrue((Path(tmp) / f"job_test_{shot.shot_id}.mp4").exists())


if __name__ == "__main__":
    unittest.main()
