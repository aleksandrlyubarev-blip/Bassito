"""
Tests for the Bassito <-> PinoCut programmatic job bridge.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_init_context_supports_custom_output_root(tmp_path):
    from bassito_core import init_context

    ctx = init_context("scene_job_001", "Create bridge shot", output_root=tmp_path)

    assert ctx.output_dir == tmp_path / "scene_job_001"
    assert ctx.output_dir.exists()


def test_submit_pinocut_job_writes_queue_manifest(tmp_path):
    from bassito_pinocut_bridge import PinoCutJobRequest, submit_pinocut_job

    request = PinoCutJobRequest(
        job_type="bridge_shot",
        prompt="Create a tense arrival bridge shot",
        scene_id="scene_03",
    )

    result = submit_pinocut_job(request, queue_root=tmp_path)

    assert result.status == "queued"
    assert Path(result.request_path).exists()

    payload = json.loads(Path(result.request_path).read_text(encoding="utf-8"))
    assert payload["job_type"] == "bridge_shot"
    assert payload["scene_id"] == "scene_03"


def test_submit_pinocut_job_can_run_stubbed_visual_path(tmp_path):
    from bassito_pinocut_bridge import PinoCutJobRequest, submit_pinocut_job

    request = PinoCutJobRequest(
        job_type="restyle",
        prompt="Restyle the clip into dark sci-fi",
        scene_id="scene_04",
        source_clip_id="c07",
        source_clip_path="clips/c07.mp4",
        style_profile="cinematic dark sci-fi",
    )

    result = submit_pinocut_job(request, queue_root=tmp_path, run_now=True)

    assert result.status == "completed_stub"
    assert Path(result.request_path).exists()
    assert Path(result.result_path).exists()
    assert Path(result.artifact_path).exists()

    artifact = json.loads(Path(result.artifact_path).read_text(encoding="utf-8"))
    assert artifact["job_type"] == "restyle"
    assert artifact["source_clip_id"] == "c07"
    assert "stubbed visual-generation phase" in artifact["note"]
