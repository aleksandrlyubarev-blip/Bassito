"""
CTA5 Controller
================
Three automation strategies for Cartoon Animator 5, tried in order:
  A) CLI Pipeline (best — headless, reliable)
  B) JavaScript Plugin API (rich control, needs GUI running)
  C) UI Automation via pyautogui (last resort, fragile)

Plus a health monitor to detect hangs and restart CTA5.

Usage:
    controller = CTA5Controller.auto_detect()
    await controller.render(project_path, output_path)
"""

import asyncio
import logging
import os
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Callable, Awaitable

logger = logging.getLogger("bassito.cta5")

# ── Config ──────────────────────────────────────────────────────────
CTA5_INSTALL_DIR = os.getenv(
    "CTA5_INSTALL_DIR",
    r"C:\Program Files\Reallusion\Cartoon Animator 5"
)
CTA5_EXE = os.path.join(CTA5_INSTALL_DIR, "CTA5.exe")
CTA5_PIPELINE_EXE = os.path.join(CTA5_INSTALL_DIR, "CTA5Pipeline.exe")
CTA5_RENDER_TIMEOUT = int(os.getenv("CTA5_RENDER_TIMEOUT_MINUTES", "30"))


# ── Exceptions ──────────────────────────────────────────────────────
class CTA5Error(Exception):
    pass


class CTA5RenderError(CTA5Error):
    pass


class CTA5TimeoutError(CTA5Error):
    pass


# ── Health Monitor ──────────────────────────────────────────────────
class CTA5HealthMonitor:
    """Monitor and manage the CTA5 process lifecycle."""

    def __init__(self, exe_path: str = CTA5_EXE):
        self.exe_path = exe_path

    def is_running(self) -> bool:
        try:
            import psutil
            for proc in psutil.process_iter(["name"]):
                if proc.info["name"] and "CTA5" in proc.info["name"]:
                    return True
        except ImportError:
            # Fallback: check via tasklist (Windows) or pgrep (Linux)
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq CTA5.exe"],
                capture_output=True, text=True
            )
            return "CTA5.exe" in result.stdout
        return False

    def ensure_running(self, startup_wait: int = 15):
        if self.is_running():
            logger.info("CTA5 is already running.")
            return

        logger.info("Starting CTA5...")
        subprocess.Popen([self.exe_path])
        time.sleep(startup_wait)

        if not self.is_running():
            raise CTA5Error(
                f"Failed to launch CTA5 from {self.exe_path}. "
                "Verify the install path and that the machine is logged in."
            )
        logger.info("CTA5 started successfully.")

    def kill(self):
        logger.warning("Force-killing CTA5...")
        try:
            import psutil
            for proc in psutil.process_iter(["name"]):
                if proc.info["name"] and "CTA5" in proc.info["name"]:
                    proc.kill()
        except ImportError:
            subprocess.run(["taskkill", "/F", "/IM", "CTA5.exe"], capture_output=True)
        time.sleep(3)

    def restart(self, startup_wait: int = 15):
        self.kill()
        self.ensure_running(startup_wait)


# ── Abstract Base ───────────────────────────────────────────────────
class BaseCTA5Controller(ABC):
    """Base class for all CTA5 automation strategies."""

    def __init__(self):
        self.monitor = CTA5HealthMonitor()

    @abstractmethod
    async def render(
        self,
        project_path: str,
        output_path: str,
        audio_path: Optional[str] = None,
        on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        """Render a CTA5 project to video. Returns the output file path."""
        ...

    @staticmethod
    def is_available() -> bool:
        """Check if this strategy can work on the current system."""
        return False


# ── Strategy A: CLI Pipeline ────────────────────────────────────────
class CLIPipelineController(BaseCTA5Controller):
    """
    Best strategy: uses CTA5's command-line pipeline renderer.
    Available in CTA5 v5.2+ (check your version).
    Fully headless — no GUI needed.
    """

    @staticmethod
    def is_available() -> bool:
        return Path(CTA5_PIPELINE_EXE).exists()

    async def render(self, project_path, output_path, audio_path=None, on_progress=None):
        if not Path(project_path).exists():
            raise FileNotFoundError(f"Project not found: {project_path}")

        cmd = [
            CTA5_PIPELINE_EXE,
            "-project", str(project_path),
            "-output", str(output_path),
            "-format", "ProRes4444",
            "-fps", "30",
        ]

        if on_progress:
            await on_progress("Launching CTA5 CLI renderer...")

        logger.info(f"CLI render: {' '.join(cmd)}")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=CTA5_RENDER_TIMEOUT * 60,
            )
        except asyncio.TimeoutError:
            process.kill()
            raise CTA5TimeoutError(f"CLI render timed out after {CTA5_RENDER_TIMEOUT} min")

        if process.returncode != 0:
            raise CTA5RenderError(
                f"CLI render failed (exit {process.returncode}):\n{stderr.decode()[:500]}"
            )

        if not Path(output_path).exists():
            raise CTA5RenderError(f"Render completed but output not found: {output_path}")

        if on_progress:
            await on_progress("CTA5 CLI render complete.")

        return output_path


# ── Strategy B: JavaScript Plugin API ───────────────────────────────
class ScriptAPIController(BaseCTA5Controller):
    """
    Uses CTA5's internal JavaScript/RLPy scripting engine.
    Requires CTA5 GUI to be running. Richer scene control than CLI.
    """

    SCRIPT_DIR = Path("cta5_scripts")
    WATCH_DIR = Path(os.getenv("CTA5_SCRIPT_WATCH_DIR", r"C:\Users\Public\Documents\Reallusion\CTA5\Scripts"))

    RENDER_TEMPLATE = """
// Auto-generated Bassito render script
var scene = RLPy.RScene;
scene.Open("{{PROJECT_PATH}}");

{{AUDIO_SECTION}}

var settings = RLPy.RRenderSetting();
settings.SetOutputFilePath("{{OUTPUT_PATH}}");
settings.SetVideoCodec(RLPy.EVideoCodec_ProRes4444);
settings.SetFPS(30);
scene.Render(settings);
"""

    AUDIO_TEMPLATE = """
var avatar = scene.FindObject("Bassito");
if (avatar) {
    RLPy.RAudioRecognition.LoadAudio(avatar, "{{AUDIO_PATH}}");
}
"""

    @staticmethod
    def is_available() -> bool:
        # Check if the CTA5 script watch directory exists
        watch = Path(os.getenv(
            "CTA5_SCRIPT_WATCH_DIR",
            r"C:\Users\Public\Documents\Reallusion\CTA5\Scripts"
        ))
        return watch.exists() and Path(CTA5_EXE).exists()

    async def render(self, project_path, output_path, audio_path=None, on_progress=None):
        self.monitor.ensure_running()

        # Build the render script
        script = self.RENDER_TEMPLATE.replace("{{PROJECT_PATH}}", str(project_path).replace("\\", "/"))
        script = script.replace("{{OUTPUT_PATH}}", str(output_path).replace("\\", "/"))

        if audio_path:
            audio_section = self.AUDIO_TEMPLATE.replace("{{AUDIO_PATH}}", str(audio_path).replace("\\", "/"))
        else:
            audio_section = ""
        script = script.replace("{{AUDIO_SECTION}}", audio_section)

        # Write script to CTA5's watch directory
        script_name = f"bassito_render_{int(time.time())}.js"
        script_path = self.WATCH_DIR / script_name
        script_path.write_text(script, encoding="utf-8")

        if on_progress:
            await on_progress("CTA5 script dispatched. Waiting for render...")

        logger.info(f"Script placed at: {script_path}")

        # Poll for output file
        output = Path(output_path)
        start = time.time()
        while not output.exists():
            if time.time() - start > CTA5_RENDER_TIMEOUT * 60:
                raise CTA5TimeoutError(f"Script render timed out after {CTA5_RENDER_TIMEOUT} min")
            await asyncio.sleep(5)

        # Verify file is complete (size stable for 5 seconds)
        prev_size = -1
        while True:
            curr_size = output.stat().st_size
            if curr_size == prev_size and curr_size > 0:
                break
            prev_size = curr_size
            await asyncio.sleep(5)

        # Cleanup script
        script_path.unlink(missing_ok=True)

        if on_progress:
            await on_progress("CTA5 script render complete.")

        return str(output)


# ── Strategy C: UI Automation (Last Resort) ─────────────────────────
class UIAutomationController(BaseCTA5Controller):
    """
    Drives CTA5 via pyautogui + pywinauto.
    WARNING: Extremely fragile. Requires:
      - Machine logged in with screen unlocked
      - CTA5 running and in a known state
      - Fixed screen resolution
    Only use if Strategies A and B are unavailable.
    """

    @staticmethod
    def is_available() -> bool:
        try:
            import pyautogui
            import pywinauto
            return Path(CTA5_EXE).exists()
        except ImportError:
            return False

    async def render(self, project_path, output_path, audio_path=None, on_progress=None):
        import pyautogui
        import pywinauto

        self.monitor.ensure_running()

        if on_progress:
            await on_progress("⚠️ Using UI automation (fragile mode)...")

        app = pywinauto.Application().connect(title_re="Cartoon Animator 5.*")
        main_win = app.top_window()
        main_win.set_focus()
        await asyncio.sleep(1)

        # Open project: Ctrl+O
        main_win.type_keys("^o")
        await asyncio.sleep(2)
        pyautogui.typewrite(str(project_path), interval=0.02)
        pyautogui.press("enter")
        await asyncio.sleep(10)  # Wait for project to load

        # Trigger render: Ctrl+Shift+R (adjust for your CTA5 version)
        main_win.type_keys("^+r")
        await asyncio.sleep(2)

        # Type output path in render dialog (this is VERY version-dependent)
        pyautogui.typewrite(str(output_path), interval=0.02)
        pyautogui.press("enter")

        # Wait for render completion by polling file
        output = Path(output_path)
        start = time.time()
        while not output.exists():
            if time.time() - start > CTA5_RENDER_TIMEOUT * 60:
                raise CTA5TimeoutError(f"UI render timed out after {CTA5_RENDER_TIMEOUT} min")
            await asyncio.sleep(10)

        # Wait for file to finish writing
        prev_size = -1
        for _ in range(60):
            curr_size = output.stat().st_size
            if curr_size == prev_size and curr_size > 0:
                break
            prev_size = curr_size
            await asyncio.sleep(5)

        if on_progress:
            await on_progress("CTA5 UI render complete.")

        return str(output)


# ── Auto-Detect Factory ────────────────────────────────────────────
class CTA5Controller:
    """Factory: selects the best available automation strategy."""

    STRATEGIES = [
        ("CLI Pipeline", CLIPipelineController),
        ("Script API", ScriptAPIController),
        ("UI Automation", UIAutomationController),
    ]

    @classmethod
    def auto_detect(cls) -> BaseCTA5Controller:
        for name, strategy_cls in cls.STRATEGIES:
            if strategy_cls.is_available():
                logger.info(f"CTA5 strategy selected: {name}")
                return strategy_cls()

        available = ", ".join(n for n, _ in cls.STRATEGIES)
        raise CTA5Error(
            f"No CTA5 automation strategy available. Checked: {available}. "
            f"Verify CTA5 is installed at {CTA5_INSTALL_DIR}."
        )

    @classmethod
    def force(cls, strategy: str) -> BaseCTA5Controller:
        """Force a specific strategy by name: 'cli', 'script', or 'ui'."""
        mapping = {
            "cli": CLIPipelineController,
            "script": ScriptAPIController,
            "ui": UIAutomationController,
        }
        if strategy not in mapping:
            raise ValueError(f"Unknown strategy '{strategy}'. Choose from: {list(mapping.keys())}")
        return mapping[strategy]()
