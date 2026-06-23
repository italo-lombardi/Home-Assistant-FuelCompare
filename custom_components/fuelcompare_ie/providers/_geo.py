"""Shared geographic helpers for providers.

Used by providers whose upstream API has no native radius parameter and must
filter station lists client-side after fetching (e.g. au_fuelwatch RSS,
at_econtrol "10 nearest" hard cap).

ponytail: 14 other providers carry private copies of haversine. Migrating
those is a refactor, not part of the radius-honour bug fix; left for a
follow-up cleanup.
"""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import Any, Iterable

_EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km between two WGS84 coords (mean-radius earth)."""
    r1 = radians(lat1)
    r2 = radians(lat2)
    dlat = r2 - r1
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(r1) * cos(r2) * sin(dlng / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * asin(sqrt(a))


def filter_within_radius(
    items: Iterable[tuple[str, dict[str, Any]]],
    lat: float | None,
    lng: float | None,
    radius_km: float | None,
    *,
    lat_key: str = "latitude",
    lng_key: str = "longitude",
) -> list[tuple[str, dict[str, Any]]]:
    """Yield only items whose (lat_key, lng_key) lie within `radius_km`.

    If lat/lng/radius_km is missing or falsy, returns the full list unchanged
    (back-compat path for callers that don't supply coordinates).

    Items whose dict has no coords (or non-numeric coords) are DROPPED when
    the filter is active — same conservative behaviour as au_fuelwatch.
    """
    materialised = list(items)
    if lat is None or lng is None or not radius_km:
        return materialised

    flat = float(lat)
    flng = float(lng)
    frad = float(radius_km)
    out: list[tuple[str, dict[str, Any]]] = []
    for sid, data in materialised:
        slat = data.get(lat_key)
        slng = data.get(lng_key)
        if slat is None or slng is None:
            continue
        try:
            d = haversine_km(flat, flng, float(slat), float(slng))
        except (TypeError, ValueError):
            continue
        if d <= frad:
            out.append((sid, data))
    return out


if __name__ == "__main__":
    # ponytail: assert-based self-check, no test framework needed
    # Perth CBD ↔ Fremantle ≈ 16 km (known great-circle distance)
    d = haversine_km(-31.9523, 115.8613, -32.0569, 115.7439)
    assert 15.5 < d < 16.5, f"haversine off: {d}"

    # Identity
    assert haversine_km(0.0, 0.0, 0.0, 0.0) == 0.0

    # filter_within_radius: no coords/radius → unchanged
    items = [("a", {"latitude": 0.0, "longitude": 0.0})]
    assert filter_within_radius(items, None, None, None) == items
    assert filter_within_radius(items, 0.0, 0.0, 0) == items

    # filter_within_radius: drops far station
    rows = [
        ("near", {"latitude": -31.95, "longitude": 115.86}),
        ("far", {"latitude": -32.06, "longitude": 115.74}),
        ("nocoords", {"latitude": None, "longitude": None}),
    ]
    kept = filter_within_radius(rows, -31.9523, 115.8613, 5.0)
    ids = [sid for sid, _ in kept]
    assert ids == ["near"], ids
    print("ok")
