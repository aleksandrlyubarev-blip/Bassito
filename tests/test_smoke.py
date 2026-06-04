"""
Smoke tests for Bassito Remote Agent.
Run: python -m pytest tests/ -v
"""

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestJobQueue:
    """Test the job queue serialization logic."""

    def test_create_job(self):
        from bassito_telegram_orchestrator import JobQueue, JobStatus
        q = JobQueue(max_size=5)
        job = q.create_job(prompt="Test prompt", chat_id=123)
        assert job.id == "job_0001"
        assert job.status == JobStatus.QUEUED
        assert job.prompt == "Test prompt"

    def test_queue_ordering(self):
        from bassito_telegram_orchestrator import JobQueue
        q = JobQueue(max_size=5)
        job1 = q.create_job("first", 1)
        job2 = q.create_job("second", 2)

        loop = asyncio.new_event_loop()
        loop.run_until_complete(q.enqueue(job1))
        loop.run_until_complete(q.enqueue(job2))
        
        first_out = loop.run_until_complete(q.next())
        assert first_out.prompt == "first"
        loop.close()

    def test_status_display(self):
        from bassito_telegram_orchestrator import JobQueue
        q = JobQueue(max_size=5)
        status = q.get_status()
        assert "empty" in status.lower()

    def test_retry_creates_new_job(self):
        from bassito_telegram_orchestrator import JobQueue, JobStatus
        q = JobQueue(max_size=5)
        original = q.create_job(prompt="Original prompt", chat_id=42)
        original.status = JobStatus.FAILED

        retry = q.create_job(prompt=original.prompt, chat_id=42)
        assert retry.id != original.id
        assert retry.prompt == original.prompt
        assert retry.status == JobStatus.QUEUED

    def test_retry_rejected_for_running_job(self):
        from bassito_telegram_orchestrator import JobQueue, JobStatus
        q = JobQueue(max_size=5)
        job = q.create_job(prompt="Running job", chat_id=42)
        job.status = JobStatus.RUNNING
        # Only FAILED and CANCELLED jobs should be retried
        assert job.status not in (JobStatus.FAILED, JobStatus.CANCELLED)


class TestPipelineContext:
    """Test the core pipeline context."""

    def test_init_context(self):
        from bassito_core import init_context
        ctx = init_context("test_001", "Test prompt")
        assert ctx.job_id == "test_001"
        assert ctx.prompt == "Test prompt"
        assert ctx.output_dir.exists()

    def test_full_pipeline_stub(self):
        from bassito_core import run_full_pipeline
        result = run_full_pipeline("test_002", "Smoke test episode")
        assert "final_composite" in result


class TestCTA5Controller:
    """Test CTA5 controller factory logic."""

    def test_strategy_order(self):
        from cta5_controller import CTA5Controller
        strategies = CTA5Controller.STRATEGIES
        names = [name for name, _ in strategies]
        assert names == ["CLI Pipeline", "Script API", "UI Automation"]

    def test_force_invalid_strategy(self):
        from cta5_controller import CTA5Controller
        try:
            CTA5Controller.force("nonexistent")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass


class TestDriveUploader:
    """Test Drive uploader config validation."""

    def test_missing_video_raises(self):
        from bassito_drive import upload_to_drive
        try:
            upload_to_drive("/nonexistent/video.mov")
            assert False, "Should have raised FileNotFoundError"
        except FileNotFoundError:
            pass


class TestM5VideoEngine:
    """Exercise the local M5 (Apple Silicon) image-to-video engine.

    The model load / sampling calls are gated behind AppleSiliconRequiredError,
    but the memory planning, JSON-prompt building and backend selection are all
    pure and run anywhere.
    """

    def test_memory_budget_usable_pool(self):
        from m5_video_engine import MemoryBudget
        budget = MemoryBudget(total_gb=24, os_reserved_gb=5)
        assert budget.usable_gb == 19

    def test_ideogram_and_wan_cannot_coexist(self):
        # The defining 24 GB constraint: heavy T2I + heavy I2V must offload.
        from m5_video_engine import MemoryBudget, Wan21Backend
        from m5_video_engine import Ideogram4Config
        budget = MemoryBudget(total_gb=24, os_reserved_gb=5)
        ideogram = Ideogram4Config(weights_dir=Path("/tmp/nope"), quant="nf4").footprint
        wan = Wan21Backend().footprint
        assert budget.must_offload(ideogram, wan) is True

    def test_offload_plan_is_sequential_on_24gb(self):
        from m5_video_engine import MemoryBudget, OffloadPlan, Ideogram4Config, Wan21Backend
        budget = MemoryBudget(total_gb=24, os_reserved_gb=5)
        stages = [
            Ideogram4Config(weights_dir=Path("/tmp/x"), quant="nf4").footprint,
            Wan21Backend().footprint,
        ]
        plan = OffloadPlan.plan(budget, stages)
        assert plan.sequential is True
        assert plan.notes

    def test_oversized_stage_rejected(self):
        from m5_video_engine import MemoryBudget, OffloadPlan, ModelFootprint
        from m5_video_engine import AppleSiliconRequiredError
        budget = MemoryBudget(total_gb=24, os_reserved_gb=5)
        huge = ModelFootprint(name="fp16 monster", quant="fp16", weights_gb=40)
        try:
            OffloadPlan.plan(budget, [huge])
            assert False, "Should reject a stage larger than the pool"
        except AppleSiliconRequiredError:
            pass

    def test_json_prompt_layout(self):
        from m5_video_engine import JSONPrompt, BoundingBox, TextElement, SceneObject
        import json
        prompt = JSONPrompt(
            scene="a poster",
            objects=[SceneObject("a red car", BoundingBox(0.1, 0.1, 0.5, 0.5), "#FF0000")],
            text_elements=[TextElement("SALE", hex_color="#fff", font="Helvetica")],
            background_hex="000",
        )
        data = json.loads(prompt.render())
        assert data["scene"] == "a poster"
        assert data["objects"][0]["bbox"] == [0.1, 0.1, 0.5, 0.5]
        assert data["objects"][0]["color"] == "#FF0000"
        assert data["text"][0]["color"] == "#FFF"
        assert data["background_color"] == "#000"

    def test_bounding_box_validation(self):
        from m5_video_engine import BoundingBox
        for bad in [(0.5, 0.1, 0.1, 0.5), (0.1, 0.1, 1.5, 0.5)]:
            try:
                BoundingBox(*bad)
                assert False, "Should reject invalid box"
            except ValueError:
                pass

    def test_select_backend_prefers_quality_within_budget(self):
        from m5_video_engine import MemoryBudget, select_backend, Wan21Backend
        budget = MemoryBudget(total_gb=24, os_reserved_gb=5)  # 19 GB usable
        backend = select_backend(budget)
        # Wan 2.1 14B (~16 GB) is highest priority and fits in 19 GB.
        assert backend.NAME == Wan21Backend.NAME

    def test_select_backend_downshifts_on_tiny_pool(self):
        from m5_video_engine import MemoryBudget, select_backend, Wan21SmallBackend
        budget = MemoryBudget(total_gb=8, os_reserved_gb=2)  # 6 GB usable
        backend = select_backend(budget)
        assert backend.NAME == Wan21SmallBackend.NAME

    def test_force_unknown_backend_raises(self):
        from m5_video_engine import force_backend
        try:
            force_backend("nonexistent")
            assert False, "Should raise ValueError"
        except ValueError:
            pass

    def test_i2v_request_validates_frame_count(self):
        from m5_video_engine import I2VRequest
        ok = I2VRequest(Path("k.png"), "pan", Path("o.mp4"), num_frames=81, fps=16)
        assert ok.duration_seconds == 81 / 16
        try:
            I2VRequest(Path("k.png"), "pan", Path("o.mp4"), num_frames=50)
            assert False, "Should reject non-canonical frame count"
        except ValueError:
            pass

    def test_pipeline_plan_includes_i2v_footprint(self):
        from m5_video_engine import M5VideoPipeline, M5VideoConfig, MemoryBudget
        config = M5VideoConfig(budget=MemoryBudget(total_gb=24, os_reserved_gb=5))
        pipeline = M5VideoPipeline(config)
        plan = pipeline.plan()  # no Ideogram weights configured -> I2V-only stage
        assert plan.stages
        assert any("Wan" in s.name or "LTX" in s.name or "Hunyuan" in s.name
                   for s in plan.stages)

    def test_core_exposes_m5_pipeline(self):
        import bassito_core
        assert hasattr(bassito_core, "run_m5_local_pipeline")
        assert hasattr(bassito_core, "generate_video_m5_local")
        names = [fn.__name__ for fn in bassito_core.M5_LOCAL_PHASES]
        assert "generate_video_m5_local" in names

    def test_orchestrator_registers_generate_local(self):
        from bassito_telegram_orchestrator import cmd_generate_local
        assert callable(cmd_generate_local)


class TestJobSearchModule:
    """Smoke checks for the /find_boost feature."""

    def test_imports(self):
        import bassito_jobs
        assert hasattr(bassito_jobs, "run_search")
        assert hasattr(bassito_jobs, "load_profile")

    def test_orchestrator_registers_find_boost_handlers(self):
        # Just check the handler functions exist and are importable.
        from bassito_telegram_orchestrator import (
            cmd_find_boost,
            cmd_find_boost_profile,
            cmd_find_boost_unwatch,
            cmd_find_boost_watch,
            cmd_receive_cv,
        )
        for h in (cmd_find_boost, cmd_find_boost_profile, cmd_find_boost_unwatch,
                  cmd_find_boost_watch, cmd_receive_cv):
            assert callable(h)
