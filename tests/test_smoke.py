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
