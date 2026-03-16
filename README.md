# Bassito Remote Agent

Remote control for the Bassito video production pipeline via Telegram, with automatic Google Drive upload.

## Architecture

```
You (anywhere) → Telegram Bot → Job Queue → Pipeline Runner
  → Phase 1: Script generation (Grok API)
  → Phase 2: Background generation (Veo/Grok)
  → Phase 3: Voice synthesis
  → Phase 4: Lip-sync generation
  → Phase 5: CTA5 render
  → Phase 6: FFmpeg compositing
  → Google Drive upload → Link back to Telegram
```

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/youruser/bassito-remote.git
cd bassito-remote
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your keys (see Configuration below)
```

### 3. Set up Google Drive

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → create project → enable Drive API
2. Create a **Service Account** → download JSON key → save as `service_account.json`
3. In Google Drive, share your target folder with the service account email
4. Copy the folder ID into `.env` → `GOOGLE_DRIVE_FOLDER_ID`

### 4. Set up Telegram Bot

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token
2. Get your Telegram ID via [@userinfobot](https://t.me/userinfobot)
3. Set both in `.env`

### 5. Run

```bash
python bassito_telegram_orchestrator.py
```

Then message your bot: `/generate Bassito argues with a cat about philosophy`

## Bot Commands

| Command | Description |
|---------|-------------|
| `/generate <prompt>` | Start a new episode |
| `/status` | Agent & current job status |
| `/queue` | View job queue |
| `/stop` | Cancel current job |
| `/last` | Link to last completed video |
| `/help` | Show all commands |

## CTA5 Automation

The system auto-detects the best strategy for controlling Cartoon Animator 5:

| Strategy | Requirements | Headless? |
|----------|-------------|-----------|
| **A: CLI Pipeline** | CTA5 v5.2+ with `CTA5Pipeline.exe` | ✅ Yes |
| **B: Script API** | CTA5 with RLPy scripting enabled | ❌ GUI needed |
| **C: UI Automation** | `pyautogui` + `pywinauto` + screen unlocked | ❌ Fragile |

Force a specific strategy via environment or code:

```python
from cta5_controller import CTA5Controller
controller = CTA5Controller.force("cli")  # "cli", "script", or "ui"
```

## Configuration

All settings in `.env`:

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | ✅ | Telegram bot token from BotFather |
| `ALLOWED_TELEGRAM_IDS` | ✅ | Comma-separated Telegram user IDs |
| `XAI_API_KEY` | ✅ | Grok API key |
| `GEMINI_API_KEY` | ✅ | Google Gemini API key |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | ✅ | Path to service account JSON |
| `GOOGLE_DRIVE_FOLDER_ID` | ✅ | Target Drive folder ID |
| `CTA5_INSTALL_DIR` | ⬚ | CTA5 install path (auto-detected) |
| `CTA5_RENDER_TIMEOUT_MINUTES` | ⬚ | Render timeout, default 30 |
| `MAX_QUEUE_SIZE` | ⬚ | Max queued jobs, default 10 |
| `JOB_TIMEOUT_MINUTES` | ⬚ | Total job timeout, default 60 |

## Running as a Service

### Linux (systemd)

```bash
# Edit bassito.service — update User, WorkingDirectory, ExecStart paths
sudo cp bassito.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bassito.service

# Check status
sudo systemctl status bassito.service
journalctl -u bassito.service -f
```

### Windows (NSSM)

```cmd
# Install NSSM: https://nssm.cc/download
nssm install BassitoAgent "C:\path\to\venv\Scripts\python.exe" "C:\path\to\bassito_telegram_orchestrator.py"
nssm set BassitoAgent AppDirectory "C:\path\to\bassito-remote"
nssm start BassitoAgent
```

## Project Structure

```
bassito-remote/
├── bassito_telegram_orchestrator.py   # Telegram bot + job queue
├── bassito_core.py                    # 6-phase pipeline (your existing code)
├── bassito_drive.py                   # Google Drive uploader (Service Account)
├── cta5_controller.py                 # CTA5 automation (3 strategies)
├── cta5_scripts/                      # Auto-generated CTA5 JS scripts
├── logs/                              # Runtime logs
├── output/                            # Rendered video output
├── tests/                             # Test suite
├── .env.example                       # Config template
├── .gitignore
├── bassito.service                    # systemd unit file
├── requirements.txt
└── README.md
```

## Integration with Existing Bassito

Wire your existing 6-phase pipeline into `bassito_core.py` by implementing `run_phase()` for each `PipelinePhase`. The orchestrator calls phases sequentially with progress callbacks — see `PipelineRunner._execute_phase()` in the orchestrator for the integration point.

## License

Private / All rights reserved.
