"""
Bassito Core Pipeline
======================
Stub module — wire in your existing 6-phase Bassito pipeline here.

Each phase function receives the accumulated context from previous phases
and returns updated context. The orchestrator calls these sequentially.

TODO: Replace the stub implementations with your actual pipeline logic.
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from openai import OpenAI

logger = logging.getLogger("bassito.core")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROK_MODEL = os.getenv("GROK_MODEL", "x-ai/grok-3")

_SCRIPT_SYSTEM_PROMPT = """\
You are the creative director for Bassito, an animated video series.
Given a scene prompt, write a tight, engaging narration script (60-120 seconds when read aloud).

Rules:
- Write in first person as Bassito's voice
- Plain narration only — no scene headings, no action lines, no speaker labels
- Vivid, punchy sentences; short paragraphs
- End with a memorable closing line
- Output only the script text, nothing else
"""


@dataclass
class PipelineContext:
    """State passed between pipeline phases."""
    job_id: str
    prompt: str
    output_dir: Path

    # Phase outputs (populated as pipeline progresses)
    script: Optional[str] = None
    background_paths: list[str] = field(default_factory=list)
    voice_path: Optional[str] = None
    lipsync_path: Optional[str] = None
    render_path: Optional[str] = None
    final_video_path: Optional[str] = None


def init_context(job_id: str, prompt: str) -> PipelineContext:
    """Create a fresh pipeline context and output directory."""
    output_dir = Path("output") / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    return PipelineContext(job_id=job_id, prompt=prompt, output_dir=output_dir)


# ── Phase 1: Script Generation ──────────────────────────────────────
def generate_script(ctx: PipelineContext) -> PipelineContext:
    """Generate episode narration script via Grok (xAI API)."""
    logger.info(f"[{ctx.job_id}] Generating script from prompt: {ctx.prompt[:80]}")

    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set in environment")

    client = OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        default_headers={"X-Title": "Bassito"},
    )

    response = client.chat.completions.create(
        model=GROK_MODEL,
        messages=[
            {"role": "system", "content": _SCRIPT_SYSTEM_PROMPT},
            {"role": "user", "content": ctx.prompt},
        ],
        temperature=0.85,
        max_tokens=1024,
    )

    script = response.choices[0].message.content.strip()

    # Persist to disk so downstream phases can reference it
    script_path = ctx.output_dir / "script.txt"
    script_path.write_text(script, encoding="utf-8")

    ctx.script = script
    logger.info(f"[{ctx.job_id}] Script generated ({len(script)} chars) → {script_path}")
    return ctx


# ── Phase 2: Background Generation ──────────────────────────────────
def generate_backgrounds(ctx: PipelineContext) -> PipelineContext:
    """
    Generate background images/video via Veo or Grok image API.
    
    TODO: Wire in your Veo/Grok background generation.
    Example:
        for scene in parse_scenes(ctx.script):
            bg = veo_client.generate(scene.description)
            bg.save(ctx.output_dir / f"bg_{scene.index}.png")
            ctx.background_paths.append(str(bg_path))
    """
    logger.info(f"[{ctx.job_id}] Generating backgrounds...")
    # STUB
    ctx.background_paths = [str(ctx.output_dir / "bg_placeholder.png")]
    return ctx


# ── Phase 3: Voice Synthesis ────────────────────────────────────────
def synthesize_voice(ctx: PipelineContext) -> PipelineContext:
    """
    Synthesize character voice from the script.
    
    TODO: Wire in your TTS pipeline.
    Example:
        audio = tts_client.synthesize(ctx.script, voice="bassito")
        audio.save(ctx.output_dir / "voice.wav")
        ctx.voice_path = str(audio_path)
    """
    logger.info(f"[{ctx.job_id}] Synthesizing voice...")
    # STUB
    ctx.voice_path = str(ctx.output_dir / "voice_placeholder.wav")
    return ctx


# ── Phase 4: Lip-Sync Generation ───────────────────────────────────
def generate_lipsync(ctx: PipelineContext) -> PipelineContext:
    """
    Generate lip-sync animation data from the voice audio.
    
    TODO: Wire in your lip-sync tool.
    Example:
        lipsync = lipsync_engine.process(ctx.voice_path, avatar="bassito")
        lipsync.save(ctx.output_dir / "lipsync.json")
        ctx.lipsync_path = str(path)
    """
    logger.info(f"[{ctx.job_id}] Generating lip-sync...")
    # STUB
    ctx.lipsync_path = str(ctx.output_dir / "lipsync_placeholder.json")
    return ctx


# ── Phase 5: CTA5 Render ───────────────────────────────────────────
def render_cta5(ctx: PipelineContext) -> PipelineContext:
    """
    Render the animated scene in CTA5.
    
    This is called by the orchestrator but delegates to cta5_controller.
    The orchestrator handles this phase separately via CTA5Controller.
    
    TODO: Build the CTA5 project file from lipsync + backgrounds,
    then let cta5_controller.render() handle the actual rendering.
    """
    logger.info(f"[{ctx.job_id}] CTA5 render (handled by cta5_controller)...")
    # STUB — orchestrator calls cta5_controller directly
    ctx.render_path = str(ctx.output_dir / "render_placeholder.mov")
    return ctx


# ── Phase 6: FFmpeg Compositing ─────────────────────────────────────
def composite_ffmpeg(ctx: PipelineContext) -> PipelineContext:
    """
    Final compositing: layer backgrounds, render, audio via FFmpeg.
    
    TODO: Wire in your FFmpeg composite command.
    Example:
        cmd = [
            "ffmpeg", "-y",
            "-i", ctx.render_path,
            "-i", ctx.background_paths[0],
            "-i", ctx.voice_path,
            "-filter_complex", "[0:v][1:v]overlay=...",
            "-c:v", "prores_ks", "-profile:v", "4",
            str(ctx.output_dir / "final_composite.mov")
        ]
        subprocess.run(cmd, check=True)
    """
    logger.info(f"[{ctx.job_id}] FFmpeg compositing...")
    # STUB
    final_path = str(ctx.output_dir / "final_composite.mov")
    ctx.final_video_path = final_path
    return ctx


# ── Full Pipeline (sequential) ──────────────────────────────────────
PHASES = [
    generate_script,
    generate_backgrounds,
    synthesize_voice,
    generate_lipsync,
    render_cta5,
    composite_ffmpeg,
]


def run_full_pipeline(job_id: str, prompt: str) -> str:
    """
    Run all 6 phases sequentially. Returns path to final video.
    Called by the orchestrator via asyncio.to_thread().
    """
    ctx = init_context(job_id, prompt)
    
    for phase_fn in PHASES:
        logger.info(f"[{ctx.job_id}] Running phase: {phase_fn.__name__}")
        ctx = phase_fn(ctx)
    
    if not ctx.final_video_path:
        raise RuntimeError("Pipeline completed but no final video path set")
    
    logger.info(f"[{ctx.job_id}] Pipeline complete: {ctx.final_video_path}")
    return ctx.final_video_path
