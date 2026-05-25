"""User profile for job matching.

Profiles live at ~/.bassito/profiles/<name>/profile.yaml. CV PDFs are stored
alongside as cv.pdf and parsed via the Anthropic API into structured fields.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .geo import GeoFilter, MIGDAL_HAEMEK

logger = logging.getLogger(__name__)

PROFILES_ROOT = Path.home() / ".bassito" / "profiles"

DEFAULT_SOURCES = ["alljobs", "geektime", "drushim", "flex", "greenhouse", "lever", "comeet"]

CV_EXTRACTION_PROMPT = """You extract a structured job-matching profile from a CV.

Output STRICT JSON only (no prose, no markdown fence) with these fields:
{
  "current_role": "<string>",
  "years_experience": <integer>,
  "skills": ["<short tag>", ...],          // 8-25 items, lowercase
  "seniority": ["senior", "lead", ...],    // valid: junior, mid, senior, lead, principal, staff
  "preferred_stack": ["<tech>", ...],      // 5-15 items
  "include_keywords": ["<phrase>", ...],   // 5-15 job-title-style phrases user wants to see
  "exclude_keywords": ["<phrase>", ...]    // 2-10 phrases to filter OUT (e.g. "intern", "qa only")
}

Focus on AI / ML / data engineering keywords if the CV shows that background.
The user wants to find SENIOR / LEAD AI engineering roles — bias include_keywords accordingly.
"""


@dataclass
class Profile:
    name: str
    current_role: str = ""
    years_experience: int = 0
    skills: list[str] = field(default_factory=list)
    seniority: list[str] = field(default_factory=lambda: ["senior", "lead", "principal"])
    preferred_stack: list[str] = field(default_factory=list)
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=lambda: ["intern", "junior", "qa only"])
    geo: GeoFilter = field(default_factory=GeoFilter)
    remote_ok: bool = False
    sources_enabled: list[str] = field(default_factory=lambda: list(DEFAULT_SOURCES))

    def to_yaml(self) -> str:
        d = asdict(self)
        # Flatten GeoFilter from dataclass to plain dict for YAML readability.
        d["geo"] = {
            "center": list(self.geo.center),
            "radius_km": self.geo.radius_km,
            "extra_allow": self.geo.extra_allow,
            "flex_haifa": self.geo.flex_haifa,
            "remote_ok": self.geo.remote_ok,
        }
        return yaml.safe_dump(d, allow_unicode=True, sort_keys=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Profile":
        geo_raw = d.get("geo") or {}
        center = geo_raw.get("center") or MIGDAL_HAEMEK
        if isinstance(center, list):
            center = tuple(center)
        geo = GeoFilter(
            center=center,
            radius_km=float(geo_raw.get("radius_km", 60.0)),
            extra_allow=list(geo_raw.get("extra_allow", [])),
            flex_haifa=bool(geo_raw.get("flex_haifa", True)),
            remote_ok=bool(geo_raw.get("remote_ok", d.get("remote_ok", False))),
        )
        return cls(
            name=d["name"],
            current_role=d.get("current_role", ""),
            years_experience=int(d.get("years_experience", 0)),
            skills=list(d.get("skills", [])),
            seniority=list(d.get("seniority", ["senior", "lead", "principal"])),
            preferred_stack=list(d.get("preferred_stack", [])),
            include_keywords=list(d.get("include_keywords", [])),
            exclude_keywords=list(d.get("exclude_keywords", ["intern", "junior", "qa only"])),
            geo=geo,
            remote_ok=bool(d.get("remote_ok", False)),
            sources_enabled=list(d.get("sources_enabled", DEFAULT_SOURCES)),
        )


def profile_dir(name: str) -> Path:
    return PROFILES_ROOT / name


def load_profile(name: str) -> Profile | None:
    path = profile_dir(name) / "profile.yaml"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("name", name)
    return Profile.from_dict(data)


def save_profile(profile: Profile) -> Path:
    d = profile_dir(profile.name)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "profile.yaml"
    path.write_text(profile.to_yaml(), encoding="utf-8")
    return path


def extract_text_from_pdf(pdf_path: Path) -> str:
    from pypdf import PdfReader  # local import to keep optional dep lazy
    reader = PdfReader(str(pdf_path))
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception as e:  # noqa: BLE001
            logger.warning("PDF page extract failed: %s", e)
    return "\n".join(parts).strip()


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def extract_profile_from_pdf(
    name: str,
    pdf_path: Path,
    *,
    model: str = "claude-opus-4-7",
    client: Any | None = None,
) -> Profile:
    """Parse CV PDF with the Anthropic API and build a Profile.

    Falls back to a stub Profile if no ANTHROPIC_API_KEY is set, so the bot
    is still usable for keyword-only matching.
    """
    text = extract_text_from_pdf(pdf_path)
    if not text:
        raise ValueError(f"No extractable text in {pdf_path}")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key and client is None:
        logger.warning("ANTHROPIC_API_KEY not set; creating profile with empty fields")
        return Profile(name=name, current_role="(set ANTHROPIC_API_KEY and re-upload CV)")

    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

    truncated = text[:30000]
    msg = client.messages.create(
        model=model,
        max_tokens=2000,
        system=CV_EXTRACTION_PROMPT,
        messages=[{"role": "user", "content": truncated}],
    )
    raw = msg.content[0].text if msg.content else "{}"
    try:
        data = json.loads(_strip_json_fences(raw))
    except json.JSONDecodeError as e:
        raise ValueError(f"Anthropic returned non-JSON profile: {e}\n{raw[:500]}") from e

    data["name"] = name
    return Profile.from_dict(data)
