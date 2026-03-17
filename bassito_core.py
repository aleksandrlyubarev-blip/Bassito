"""
Bassito Core Pipeline
======================
Stub module — wire in your existing 6-phase Bassito pipeline here.

Each phase function receives the accumulated context from previous phases
and returns updated context. The orchestrator calls these sequentially.

TODO: Replace the stub implementations with your actual pipeline logic.
"""

import base64
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types as genai_types
from openai import OpenAI

logger = logging.getLogger("bassito.core")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GROK_MODEL = os.getenv("GROK_MODEL", "x-ai/grok-3")
IMAGEN_MODEL = os.getenv("IMAGEN_MODEL", "imagen-3.0-generate-002")
TTS_MODEL = os.getenv("TTS_MODEL", "openai/tts-1-hd")
TTS_VOICE = os.getenv("TTS_VOICE", "fable")

# Number of background images to generate per job
BG_COUNT = int(os.getenv("BG_COUNT", "3"))

_BG_PROMPT_SYSTEM = """\
You are a visual director for an animated video series called Bassito.
Given a narration script, extract {n} distinct scene descriptions suitable for background image generation.
Each description should be cinematic, vivid, and environment-focused (no characters).
Output exactly {n} lines, one description per line, nothing else.
"""

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
def _extract_scene_prompts(script: str, n: int, openrouter_client: OpenAI) -> list[str]:
    """Use Grok to derive n cinematic scene descriptions from the script."""
    system = _BG_PROMPT_SYSTEM.format(n=n)
    response = openrouter_client.chat.completions.create(
        model=GROK_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": script},
        ],
        temperature=0.7,
        max_tokens=512,
    )
    raw = response.choices[0].message.content.strip()
    # Split on newlines, drop empty lines, take up to n
    prompts = [line.strip() for line in raw.splitlines() if line.strip()]
    return prompts[:n] if len(prompts) >= n else prompts


def generate_backgrounds(ctx: PipelineContext) -> PipelineContext:
    """Generate background images via Google Imagen 3."""
    logger.info(f"[{ctx.job_id}] Generating backgrounds...")

    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not set in environment")
    if not ctx.script:
        raise RuntimeError("Phase 2 requires ctx.script (run Phase 1 first)")

    # Step 1: derive scene prompts from the script
    openrouter_client = OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        default_headers={"X-Title": "Bassito"},
    )
    scene_prompts = _extract_scene_prompts(ctx.script, BG_COUNT, openrouter_client)
    if not scene_prompts:
        raise RuntimeError("Failed to extract scene prompts from script")

    logger.info(f"[{ctx.job_id}] Scene prompts: {scene_prompts}")

    # Step 2: generate each background with Imagen
    imagen_client = genai.Client(api_key=GOOGLE_API_KEY)

    paths = []
    for i, prompt in enumerate(scene_prompts):
        result = imagen_client.models.generate_images(
            model=IMAGEN_MODEL,
            prompt=prompt,
            config=genai_types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="16:9",
                output_mime_type="image/png",
            ),
        )
        image_data = result.generated_images[0].image.image_bytes
        bg_path = ctx.output_dir / f"bg_{i:02d}.png"
        bg_path.write_bytes(image_data)
        paths.append(str(bg_path))
        logger.info(f"[{ctx.job_id}] Background {i} saved → {bg_path}")

    ctx.background_paths = paths
    return ctx


# ── Phase 3: Voice Synthesis ────────────────────────────────────────
def synthesize_voice(ctx: PipelineContext) -> PipelineContext:
    """Synthesize character voice from the script via OpenAI TTS (OpenRouter)."""
    logger.info(f"[{ctx.job_id}] Synthesizing voice...")

    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set in environment")
    if not ctx.script:
        raise RuntimeError("Phase 3 requires ctx.script (run Phase 1 first)")

    client = OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        default_headers={"X-Title": "Bassito"},
    )

    response = client.audio.speech.create(
        model=TTS_MODEL,
        voice=TTS_VOICE,
        input=ctx.script,
        response_format="mp3",
    )

    voice_path = ctx.output_dir / "voice.mp3"
    voice_path.write_bytes(response.content)

    ctx.voice_path = str(voice_path)
    logger.info(f"[{ctx.job_id}] Voice saved → {voice_path}")
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
