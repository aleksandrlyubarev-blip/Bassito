"""
Bassito <-> PinoCut bridge
==========================

Programmatic entrypoint for PinoCut scene jobs that should be executed by
Bassito's visual pipeline instead of the Telegram bot.

This bridge does not replace the Telegram orchestrator. It offers a typed,
machine-to-machine contract for the three PinoCut-side generation requests:

- bridge_shot
- extend
- restyle

Current implementation is intentionally honest: it writes queue/result
manifests and can optionally execute the existing stubbed visual-generation
phase immediately (`--run-now`) so the integration contract exists before the
full Bassito production pipeline is wired in.
"""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import bassito_core

JobType = Literal["bridge_shot", "extend", "restyle"]

DEFAULT_QUEUE_ROOT = Path("output") / "pinocut_jobs"


@dataclass(slots=True)
class PinoCutJobRequest:
    job_type: JobType
    prompt: str
    scene_id: str
    job_id: str = ""
    source_clip_id: str | None = None
    source_clip_path: str | None = None
    style_profile: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    def normalized(self) -> "PinoCutJobRequest":
        if self.job_id:
            return self
        return PinoCutJobRequest(
            job_type=self.job_type,
            prompt=self.prompt,
            scene_id=self.scene_id,
            job_id=f"pinocut_{uuid.uuid4().hex[:10]}",
            source_clip_id=self.source_clip_id,
            source_clip_path=self.source_clip_path,
            style_profile=self.style_profile,
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class PinoCutJobResult:
    job_id: str
    scene_id: str
    job_type: JobType
    status: str
    request_path: str
    result_path: str
    output_dir: str
    artifact_path: str | None = None
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def submit_pinocut_job(
    request: PinoCutJobRequest,
    *,
    queue_root: Path = DEFAULT_QUEUE_ROOT,
    run_now: bool = False,
) -> PinoCutJobResult:
    """Submit a PinoCut generation request to Bassito."""
    request = request.normalized()
    queue_root.mkdir(parents=True, exist_ok=True)
    queued_dir = queue_root / "queued"
    queued_dir.mkdir(parents=True, exist_ok=True)
    request_path = queued_dir / f"{request.job_id}.request.json"
    request_path.write_text(json.dumps(request.to_dict(), indent=2), encoding="utf-8")

    if run_now:
        return run_pinocut_job(request, queue_root=queue_root, request_path=request_path)

    return PinoCutJobResult(
        job_id=request.job_id,
        scene_id=request.scene_id,
        job_type=request.job_type,
        status="queued",
        request_path=str(request_path),
        result_path="",
        output_dir=str(queue_root / request.job_id),
        warnings=["Job accepted by Bassito bridge and queued for execution."],
        metadata={"execution_mode": "queued_only"},
    )


def run_pinocut_job(
    request: PinoCutJobRequest,
    *,
    queue_root: Path = DEFAULT_QUEUE_ROOT,
    request_path: Path | None = None,
) -> PinoCutJobResult:
    """
    Execute the current stubbed Bassito visual path for a PinoCut job.

    This runs the closest available Bassito phase today: background generation,
    then materializes a result manifest describing the requested visual action.
    """
    request = request.normalized()
    queue_root.mkdir(parents=True, exist_ok=True)
    completed_dir = queue_root / "completed"
    completed_dir.mkdir(parents=True, exist_ok=True)

    ctx = bassito_core.init_context(
        request.job_id,
        _build_visual_prompt(request),
        output_root=queue_root,
    )
    ctx = bassito_core.generate_backgrounds(ctx)

    artifact_path = ctx.output_dir / f"{request.job_type}.artifact.json"
    artifact_payload = {
        "job_id": request.job_id,
        "scene_id": request.scene_id,
        "job_type": request.job_type,
        "prompt": request.prompt,
        "style_profile": request.style_profile,
        "source_clip_id": request.source_clip_id,
        "source_clip_path": request.source_clip_path,
        "planned_background_paths": ctx.background_paths,
        "note": (
            "Bassito bridge executed the current stubbed visual-generation phase. "
            "Replace this artifact with real generated media once the production "
            "pipeline is wired into bassito_core."
        ),
        "metadata": request.metadata,
    }
    artifact_path.write_text(json.dumps(artifact_payload, indent=2), encoding="utf-8")

    result_path = completed_dir / f"{request.job_id}.result.json"
    result = PinoCutJobResult(
        job_id=request.job_id,
        scene_id=request.scene_id,
        job_type=request.job_type,
        status="completed_stub",
        request_path=str(request_path or ""),
        result_path=str(result_path),
        output_dir=str(ctx.output_dir),
        artifact_path=str(artifact_path),
        warnings=[
            "Bassito executed the stubbed visual bridge path only.",
            "Wire real Veo/Grok/CTA5 phases into bassito_core for production output.",
        ],
        metadata={
            "planned_background_paths": ctx.background_paths,
            "execution_mode": "run_now_stub",
        },
    )
    result_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return result


def load_request(request_path: Path) -> PinoCutJobRequest:
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    return PinoCutJobRequest(**payload)


def _build_visual_prompt(request: PinoCutJobRequest) -> str:
    source = f" for clip {request.source_clip_id}" if request.source_clip_id else ""
    style = f" | style={request.style_profile}" if request.style_profile else ""
    return f"{request.job_type}{source}: {request.prompt}{style}"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bassito_pinocut_bridge",
        description="Programmatic PinoCut job bridge for Bassito",
    )
    parser.add_argument("command", choices=["submit"], help="Bridge command")
    parser.add_argument("request_json", type=Path, help="Path to a PinoCut job request JSON")
    parser.add_argument(
        "--queue-root",
        type=Path,
        default=DEFAULT_QUEUE_ROOT,
        help="Directory where queue/result manifests should be stored",
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Run the current stubbed visual path immediately instead of queue-only mode",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    request = load_request(args.request_json)
    result = submit_pinocut_job(
        request,
        queue_root=args.queue_root,
        run_now=args.run_now,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
