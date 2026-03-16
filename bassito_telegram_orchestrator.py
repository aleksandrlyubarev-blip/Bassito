"""
Bassito Telegram Orchestrator (Improved)
=========================================
Remote control for the Bassito video pipeline via Telegram.
Features: job queue, per-phase progress, error recovery, access control.
"""

import asyncio
import logging
import os
import traceback
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ContextTypes
)
from dotenv import load_dotenv

from bassito_drive import upload_to_drive
import bassito_core
from cta5_controller import CTA5Controller

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_IDS = {int(uid.strip()) for uid in os.getenv("ALLOWED_TELEGRAM_IDS", "").split(",") if uid.strip()}
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "10"))
JOB_TIMEOUT_MINUTES = int(os.getenv("JOB_TIMEOUT_MINUTES", "60"))

# ── Logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bassito_bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("bassito")


# ── Data Models ─────────────────────────────────────────────────────
class JobStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PipelinePhase(Enum):
    SCRIPT = ("1/6", "Generating script", "📝")
    BACKGROUNDS = ("2/6", "Creating backgrounds (Veo/Grok)", "🖼️")
    VOICE = ("3/6", "Synthesizing voice", "🎙️")
    LIPSYNC = ("4/6", "Generating lip-sync", "🗣️")
    RENDER = ("5/6", "Rendering in CTA5", "🎬")
    COMPOSITE = ("6/6", "FFmpeg compositing", "🎞️")

    def __init__(self, step: str, description: str, emoji: str):
        self.step = step
        self.description = description
        self.emoji = emoji


@dataclass
class Job:
    id: str
    prompt: str
    chat_id: int
    status: JobStatus = JobStatus.QUEUED
    current_phase: Optional[PipelinePhase] = None
    created_at: datetime = field(default_factory=datetime.now)
    result_path: Optional[str] = None
    drive_link: Optional[str] = None
    error: Optional[str] = None


# ── Job Queue ───────────────────────────────────────────────────────
class JobQueue:
    """Serialized job queue — only one pipeline runs at a time."""

    def __init__(self, max_size: int = MAX_QUEUE_SIZE):
        self._queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=max_size)
        self._jobs: dict[str, Job] = {}
        self._current: Optional[Job] = None
        self._counter = 0

    def create_job(self, prompt: str, chat_id: int) -> Job:
        self._counter += 1
        job = Job(
            id=f"job_{self._counter:04d}",
            prompt=prompt,
            chat_id=chat_id,
        )
        self._jobs[job.id] = job
        return job

    async def enqueue(self, job: Job) -> int:
        """Returns position in queue (0 = will run next)."""
        await self._queue.put(job)
        return self._queue.qsize()

    async def next(self) -> Job:
        job = await self._queue.get()
        self._current = job
        job.status = JobStatus.RUNNING
        return job

    def complete(self, job: Job):
        self._current = None
        self._queue.task_done()

    @property
    def current(self) -> Optional[Job]:
        return self._current

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    def get_status(self) -> str:
        lines = []
        if self._current:
            phase = self._current.current_phase
            phase_str = f" — {phase.emoji} {phase.description}" if phase else ""
            lines.append(f"▶️ Running: {self._current.id}{phase_str}")
        if self._queue.empty():
            lines.append("📭 Queue is empty." if not self._current else "No jobs waiting.")
        else:
            lines.append(f"⏳ {self._queue.qsize()} job(s) waiting.")
        return "\n".join(lines)

    def cancel_current(self) -> bool:
        if self._current and self._current.status == JobStatus.RUNNING:
            self._current.status = JobStatus.CANCELLED
            return True
        return False


# ── Pipeline Runner ─────────────────────────────────────────────────
class PipelineRunner:
    """
    Wraps the existing Bassito 6-phase pipeline with:
    - Per-phase progress callbacks
    - Error capture and reporting
    - Timeout enforcement
    """

    def __init__(self, progress_callback: Callable):
        self.on_progress = progress_callback
        self._contexts: dict[str, bassito_core.PipelineContext] = {}
        self._cta5: Optional[object] = None

    async def run(self, job: Job) -> str:
        """Execute all 6 phases. Returns path to final video."""
        phases = list(PipelinePhase)

        for phase in phases:
            job.current_phase = phase
            await self.on_progress(job, f"{phase.emoji} Phase {phase.step}: {phase.description}...")

            try:
                result = await asyncio.wait_for(
                    self._execute_phase(phase, job),
                    timeout=JOB_TIMEOUT_MINUTES * 60 / len(phases),
                )
            except asyncio.TimeoutError:
                raise RuntimeError(f"Phase {phase.step} timed out after {JOB_TIMEOUT_MINUTES // len(phases)} min")
            except Exception as e:
                raise RuntimeError(f"Phase {phase.step} ({phase.description}) failed: {e}")

        # Return the final composite video path
        output_dir = Path("output") / job.id
        return str(output_dir / "final_composite.mov")

    async def _execute_phase(self, phase: PipelinePhase, job: Job):
        """Execute a single pipeline phase by delegating to bassito_core."""
        if phase == PipelinePhase.SCRIPT:
            ctx = bassito_core.init_context(job.id, job.prompt)
            self._contexts[job.id] = ctx
            await asyncio.to_thread(bassito_core.generate_script, ctx)

        elif phase == PipelinePhase.BACKGROUNDS:
            ctx = self._contexts[job.id]
            await asyncio.to_thread(bassito_core.generate_backgrounds, ctx)

        elif phase == PipelinePhase.VOICE:
            ctx = self._contexts[job.id]
            await asyncio.to_thread(bassito_core.synthesize_voice, ctx)

        elif phase == PipelinePhase.LIPSYNC:
            ctx = self._contexts[job.id]
            await asyncio.to_thread(bassito_core.generate_lipsync, ctx)

        elif phase == PipelinePhase.RENDER:
            ctx = self._contexts[job.id]
            if self._cta5 is None:
                self._cta5 = CTA5Controller.auto_detect()
            project_path = str(ctx.output_dir / "project.cta5")
            output_path = str(ctx.output_dir / "render.mov")
            rendered = await self._cta5.render(
                project_path=project_path,
                output_path=output_path,
                audio_path=ctx.voice_path,
                on_progress=lambda msg: self.on_progress(job, msg),
            )
            ctx.render_path = rendered

        elif phase == PipelinePhase.COMPOSITE:
            ctx = self._contexts[job.id]
            await asyncio.to_thread(bassito_core.composite_ffmpeg, ctx)

        logger.info(f"[{job.id}] Completed phase: {phase.description}")


# ── Auth ────────────────────────────────────────────────────────────
def is_authorized(user_id: int) -> bool:
    return user_id in ALLOWED_IDS


# ── Bot Handlers ────────────────────────────────────────────────────
job_queue = JobQueue()


async def send_progress(job: Job, message: str):
    """Callback: send phase progress to the user's Telegram chat."""
    bot = _bot_instance
    if bot:
        await bot.send_message(chat_id=job.chat_id, text=message)


async def worker(app: Application):
    """Background worker: pulls jobs from the queue and runs them."""
    global _bot_instance
    _bot_instance = app.bot

    runner = PipelineRunner(progress_callback=send_progress)

    while True:
        job = await job_queue.next()
        logger.info(f"Starting job {job.id}: {job.prompt[:80]}")

        try:
            video_path = await runner.run(job)
            job.result_path = video_path

            # Upload to Google Drive
            await send_progress(job, "☁️ Uploading to Google Drive...")
            drive_link = await asyncio.to_thread(upload_to_drive, video_path)
            job.drive_link = drive_link
            job.status = JobStatus.COMPLETED

            await send_progress(
                job,
                f"🎉 Done!\n"
                f"📎 {drive_link}\n"
                f"Job: {job.id}"
            )
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            logger.error(f"Job {job.id} failed: {e}\n{traceback.format_exc()}")
            await send_progress(
                job,
                f"❌ Job {job.id} failed at phase "
                f"{job.current_phase.step if job.current_phase else '?'}:\n"
                f"{e}\n\n"
                f"Use /retry {job.id} to retry."
            )
        finally:
            job_queue.complete(job)


async def cmd_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return

    prompt = " ".join(context.args) if context.args else ""
    if not prompt:
        await update.message.reply_text(
            "Usage: /generate <scenario description>\n"
            "Example: /generate Bassito argues with a cat about philosophy"
        )
        return

    job = job_queue.create_job(prompt=prompt, chat_id=update.effective_chat.id)

    try:
        position = await job_queue.enqueue(job)
        msg = f"✅ Job {job.id} created.\n📝 Prompt: {prompt[:200]}"
        if position > 1:
            msg += f"\n⏳ Position in queue: {position}"
        else:
            msg += "\n🚀 Starting now..."
        await update.message.reply_text(msg)
    except asyncio.QueueFull:
        await update.message.reply_text(
            f"❌ Queue is full ({MAX_QUEUE_SIZE} jobs). Try again later."
        )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(f"🤖 Bassito Agent\n\n{job_queue.get_status()}")


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(job_queue.get_status())


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    if job_queue.cancel_current():
        await update.message.reply_text("🛑 Cancellation requested for current job.")
    else:
        await update.message.reply_text("No job is currently running.")


async def cmd_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    # Find last completed job
    completed = [
        j for j in job_queue._jobs.values()
        if j.status == JobStatus.COMPLETED and j.drive_link
    ]
    if completed:
        last = max(completed, key=lambda j: j.created_at)
        await update.message.reply_text(f"📎 Last completed: {last.drive_link}")
    else:
        await update.message.reply_text("No completed jobs yet.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(
        "🤖 Bassito Remote Agent\n\n"
        "/generate <prompt> — Start a new episode\n"
        "/status — Agent & current job status\n"
        "/queue — View job queue\n"
        "/stop — Cancel current job\n"
        "/last — Link to last completed video\n"
        "/help — This message"
    )


# ── Main ────────────────────────────────────────────────────────────
_bot_instance = None


async def post_init(app: Application):
    """Start the background worker after the bot initializes."""
    asyncio.create_task(worker(app))
    logger.info("Bassito worker started. Waiting for jobs...")


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN not set in .env")
    if not ALLOWED_IDS:
        raise ValueError("ALLOWED_TELEGRAM_IDS not set in .env")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("generate", cmd_generate))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("last", cmd_last))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))

    logger.info("Bassito Telegram bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
