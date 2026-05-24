"""Geographic filter for Israeli job locations."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

MIGDAL_HAEMEK = (32.6857, 35.2354)

# Whitelist of Israeli cities with (lat, lon). Names are lowercased; aliases
# point at the same coordinates. Coverage focuses on the north + center where
# most relevant tech jobs sit.
IL_CITIES: dict[str, tuple[float, float]] = {
    "migdal haemek": MIGDAL_HAEMEK,
    "migdal ha'emek": MIGDAL_HAEMEK,
    "מגדל העמק": MIGDAL_HAEMEK,
    "haifa": (32.7940, 34.9896),
    "חיפה": (32.7940, 34.9896),
    "yokneam": (32.6597, 35.1058),
    "yoqneam": (32.6597, 35.1058),
    "יקנעם": (32.6597, 35.1058),
    "nesher": (32.7707, 35.0436),
    "tirat carmel": (32.7621, 34.9714),
    "kiryat ata": (32.8081, 35.1058),
    "kiryat motzkin": (32.8362, 35.0716),
    "kiryat bialik": (32.8275, 35.0856),
    "kiryat yam": (32.8475, 35.0686),
    "afula": (32.6097, 35.2890),
    "עפולה": (32.6097, 35.2890),
    "nazareth": (32.7021, 35.2978),
    "nof hagalil": (32.7099, 35.3148),
    "nazareth illit": (32.7099, 35.3148),
    "tiberias": (32.7959, 35.5318),
    "karmiel": (32.9171, 35.3038),
    "akko": (32.9281, 35.0815),
    "acre": (32.9281, 35.0815),
    "nahariya": (33.0085, 35.0978),
    "tel aviv": (32.0853, 34.7818),
    "tel-aviv": (32.0853, 34.7818),
    "תל אביב": (32.0853, 34.7818),
    "ramat gan": (32.0823, 34.8141),
    "givatayim": (32.0719, 34.8108),
    "herzliya": (32.1663, 34.8439),
    "petah tikva": (32.0840, 34.8878),
    "petach tikva": (32.0840, 34.8878),
    "petah tikvah": (32.0840, 34.8878),
    "raanana": (32.1847, 34.8708),
    "kfar saba": (32.1782, 34.9070),
    "netanya": (32.3215, 34.8532),
    "rehovot": (31.8928, 34.8113),
    "rishon lezion": (31.9730, 34.8066),
    "modiin": (31.8969, 35.0095),
    "jerusalem": (31.7683, 35.2137),
    "ירושלים": (31.7683, 35.2137),
    "beer sheva": (31.2518, 34.7913),
    "ofakim": (31.3128, 34.6196),
    "ashdod": (31.7940, 34.6446),
    "ashkelon": (31.6688, 34.5742),
    "eilat": (29.5577, 34.9519),
}


@dataclass
class GeoFilter:
    center: tuple[float, float] = MIGDAL_HAEMEK
    radius_km: float = 60.0
    extra_allow: list[str] = field(default_factory=list)
    flex_haifa: bool = True
    remote_ok: bool = False


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in kilometers."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def normalize_city(text: str) -> str | None:
    """Pick the first known city name found inside an arbitrary location string."""
    if not text:
        return None
    low = text.lower()
    # Prefer longer matches first (so "tel aviv" wins over "tel").
    for name in sorted(IL_CITIES.keys(), key=len, reverse=True):
        if name in low:
            return name
    return None


_REMOTE_RE = re.compile(r"\b(remote|hybrid|work\s*from\s*home|wfh|מרחוק|היברידי)\b", re.I)
_ISRAEL_RE = re.compile(r"\b(israel|ישראל|il)\b", re.I)


def passes(location: str, company: str, geo: GeoFilter) -> bool:
    """Return True if the job location should be included by the geo filter."""
    if not location:
        return False
    loc = location.lower()

    if geo.flex_haifa and "flex" in (company or "").lower():
        if "haifa" in loc or "ofakim" in loc or _ISRAEL_RE.search(loc):
            return True

    if geo.remote_ok and _REMOTE_RE.search(loc):
        return True

    city = normalize_city(loc)
    if city is None:
        return False

    if city in {c.lower() for c in geo.extra_allow}:
        return True

    coords = IL_CITIES[city]
    return haversine_km(geo.center, coords) <= geo.radius_km
