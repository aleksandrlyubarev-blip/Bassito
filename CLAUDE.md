# CLAUDE.md — Bassito Remote Agent

## Project Overview

**Bassito** is a Telegram-controlled video production pipeline orchestrator. Users send a prompt via Telegram; the bot runs a 6-phase pipeline (script → backgrounds → voice → lip-sync → CTA5 render → FFmpeg compositing), uploads the result to Google Drive, and returns a shareable link.

**Language**: Python 3.x (pure Python, no build system)
**Entry point**: `bassito_telegram_orchestrator.py`

---

## Repository Structure

```
Bassito/
├── bassito_telegram_orchestrator.py  # Main entry point: Telegram bot + job queue + orchestration
├── bassito_core.py                   # 6-phase pipeline functions (stubs awaiting real implementation)
├── bassito_drive.py                  # Google Drive uploader (Service Account, headless)
├── cta5_controller.py                # Cartoon Animator 5 automation (3 strategies)
├── requirements.txt                  # Python dependencies
├── .env.example                      # Environment variable template (copy to .env)
├── bassito.service                   # systemd service unit file (Linux deployment)
├── README.md                         # User-facing setup and usage documentation
├── tests/
│   ├── __init__.py
│   └── test_smoke.py                 # pytest smoke tests
├── cta5_scripts/                     # Auto-generated CTA5 JS scripts (gitignored)
│   └── .gitkeep
└── logs/                             # Runtime logs (gitignored)
    └── .gitkeep
```

---

## Architecture

```
Telegram User
    │  /generate <prompt>
    ▼
Telegram Bot (python-telegram-bot)
    │
    ▼
JobQueue  ─── serialized: one pipeline runs at a time ───►  Job (dataclass)
    │                                                         ├── id, prompt, chat_id
    ▼                                                         ├── status (JobStatus enum)
PipelineRunner                                               ├── current_phase
    │                                                         └── timestamps / results
    ├── Phase 1: generate_script()      (bassito_core.py)
    ├── Phase 2: generate_backgrounds() (bassito_core.py)
    ├── Phase 3: synthesize_voice()     (bassito_core.py)
    ├── Phase 4: generate_lipsync()     (bassito_core.py)
    ├── Phase 5: render_cta5()          (bassito_core.py → cta5_controller.py)
    └── Phase 6: composite_ffmpeg()     (bassito_core.py)
         │
         ▼
    Google Drive Upload (bassito_drive.py)
         │
         ▼
    Shareable link → Telegram reply
```

---

## Key Modules

### `bassito_telegram_orchestrator.py` — Main Entry Point
- **`JobStatus`** enum: `QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`
- **`PipelinePhase`** enum: 6 phases, each with emoji label and description
- **`Job`** dataclass: full job state (id, prompt, chat_id, status, phase, timestamps)
- **`JobQueue`**: serialized queue — only one job runs at a time
- **`PipelineRunner`**: executes phases sequentially with progress callbacks and timeouts
- **Bot commands**: `/generate`, `/status`, `/queue`, `/stop`, `/retry`, `/last`, `/help`

### `bassito_core.py` — Pipeline Implementation
- **`PipelineContext`** dataclass: state object passed between pipeline phases
- **`init_context()`**: creates per-job output directory
- **6 phase functions** (currently stubs): `generate_script()`, `generate_backgrounds()`, `synthesize_voice()`, `generate_lipsync()`, `render_cta5()`, `composite_ffmpeg()`
- **`run_full_pipeline()`**: orchestrates all 6 phases in sequence

> **Note**: All 6 phase functions are stubs. Real AI API integrations (Grok, Gemini/Veo, etc.) and FFmpeg compositing must be implemented here.

### `cta5_controller.py` — Cartoon Animator 5 Automation
Three strategies, auto-detected in priority order:

| Strategy | Class | Method | Reliability |
|----------|-------|--------|-------------|
| A | `CLIPipelineController` | `CTA5Pipeline.exe` headless CLI | Best (use first) |
| B | `ScriptAPIController` | RLPy JavaScript API (CTA5 must be running) | Good |
| C | `UIAutomationController` | pyautogui + pywinauto keyboard shortcuts | Fragile (last resort) |

- **`CTA5Controller.auto_detect()`**: factory that picks the best available strategy
- **`CTA5Controller.force(strategy)`**: force a specific strategy
- **`CTA5HealthMonitor`**: monitors the CTA5 process, restarts if needed

### `bassito_drive.py` — Google Drive Upload
- Uses **Service Account** (no OAuth prompts, fully headless)
- Resumable uploads with 50 MB chunks (suitable for large video files)
- Makes uploaded files publicly readable (shareable link)
- Auto-detects MIME type from file extension

---

## Environment Configuration

Copy `.env.example` to `.env` and fill in values:

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | Yes | Telegram bot token from @BotFather |
| `ALLOWED_TELEGRAM_IDS` | Yes | Comma-separated Telegram user IDs (access control) |
| `XAI_API_KEY` | For script gen | Grok API key |
| `GEMINI_API_KEY` | For backgrounds | Gemini/Veo API key |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Yes | Path to Google Service Account JSON |
| `GOOGLE_DRIVE_FOLDER_ID` | Yes | Target Drive folder ID |
| `CTA5_INSTALL_DIR` | For CTA5 render | Path to Cartoon Animator 5 install |
| `CTA5_SCRIPT_WATCH_DIR` | For Strategy B | CTA5 script watch directory |
| `CTA5_RENDER_TIMEOUT_MINUTES` | No | Default: 30 |
| `MAX_QUEUE_SIZE` | No | Default: 10 |
| `JOB_TIMEOUT_MINUTES` | No | Default: 60 |

**Never commit**: `.env`, `service_account.json`, `credentials.json`, `token.json`

---

## Development Workflows

### Setup

```bash
git clone <repo>
cd Bassito
python -m venv venv
source venv/bin/activate      # Linux/Mac
# venv\Scripts\activate       # Windows
pip install -r requirements.txt
cp .env.example .env
# Edit .env with real values
```

### Running

```bash
python bassito_telegram_orchestrator.py
```

### Running Tests

```bash
python -m pytest tests/ -v
```

Tests use `unittest.mock` to patch external services. They cover:
- `TestJobQueue`: queue serialization, ordering, status display
- `TestPipelineContext`: context init, full pipeline stub
- `TestCTA5Controller`: strategy factory, ordering, error handling
- `TestDriveUploader`: config validation

### Deploying as a systemd Service

```bash
# Edit bassito.service to set correct User, WorkingDirectory, ExecStart
sudo cp bassito.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable bassito
sudo systemctl start bassito
# View logs:
sudo journalctl -u bassito -f
```

---

## Code Conventions

- **Async throughout**: All bot handlers and pipeline phases use `async/await` (asyncio)
- **Dataclasses**: `Job`, `PipelineContext` — use for structured state, not plain dicts
- **Strategy pattern**: `BaseCTA5Controller` ABC — new CTA5 strategies must extend this
- **Factory pattern**: `CTA5Controller.auto_detect()` / `.force()` — do not instantiate strategies directly
- **Progress callbacks**: `PipelineRunner` accepts a `progress_callback` coroutine — use it to send Telegram updates
- **Timeouts**: Each phase enforces a timeout via `asyncio.wait_for`; respect per-phase limits
- **Error handling**: Exceptions in a phase set `job.status = FAILED`; don't swallow exceptions silently
- **One job at a time**: `JobQueue` is intentionally serialized — do not add concurrency without understanding downstream CTA5/GPU constraints

---

## Implementing Pipeline Phases

The 6 stub functions in `bassito_core.py` are the primary area for new implementation work. Each function receives a `PipelineContext` and should:

1. Set `context.<phase_output_field>` with results
2. Raise an exception on failure (the runner will catch it and mark the job failed)
3. Remain `async` — use `asyncio.to_thread()` for blocking calls (file I/O, subprocess, API calls)

Example pattern for implementing a phase:

```python
async def generate_script(context: PipelineContext) -> PipelineContext:
    # Call external API (use asyncio.to_thread for blocking SDKs)
    result = await asyncio.to_thread(_call_grok_api, context.prompt)
    context.script = result
    return context
```

---

## Sensitive Files (Never Commit)

- `.env` — contains API keys and bot token
- `service_account.json` — Google Service Account credentials
- `credentials.json`, `token.json` — OAuth tokens
- `logs/*.log` — runtime logs
- `cta5_scripts/*.js` — auto-generated, runtime artifacts
- `output/` — generated video files

---

## Current Status

- **Bot & orchestration**: Fully implemented (`bassito_telegram_orchestrator.py`)
- **CTA5 automation**: Fully implemented (`cta5_controller.py`)
- **Google Drive upload**: Fully implemented (`bassito_drive.py`)
- **Pipeline phases**: All 6 are **stubs** in `bassito_core.py` — primary area for new work
- **Tests**: Smoke tests present; expand coverage when implementing phases
