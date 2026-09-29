"""Geodesic measurement. Using pyproj.Geod makes the degrees-as-metres bug impossible."""
from __future__ import annotations

import math

from pyproj import Geod

_GEOD = Geod(ellps="WGS84")


def polygon_area_km2(geojson: dict) -> float:
    ring = geojson["coordinates"][0]
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    area_m2, _ = _GEOD.polygon_area_perimeter(lons, lats)
    return abs(area_m2) / 1e6


def bbox(geojson: dict) -> list[float]:
    ring = geojson["coordinates"][0]
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return [min(xs), min(ys), max(xs), max(ys)]


def utm_crs_for(geojson: dict) -> str:
    x0, y0, x1, y1 = bbox(geojson)
    lon, lat = (x0 + x1) / 2, (y0 + y1) / 2
    zone = int((lon + 180) / 6) + 1
    return f"EPSG:{(326 if lat >= 0 else 327)}{zone:02d}"


def estimate_pixels(geojson: dict, gsd_m: float) -> tuple[int, int]:
    x0, y0, x1, y1 = bbox(geojson)
    width_m, _, _ = _GEOD.inv(x0, (y0 + y1) / 2, x1, (y0 + y1) / 2), None, None
    wm = abs(_GEOD.inv(x0, (y0 + y1) / 2, x1, (y0 + y1) / 2)[2])
    hm = abs(_GEOD.inv((x0 + x1) / 2, y0, (x0 + x1) / 2, y1)[2])
    return max(1, int(wm / gsd_m)), max(1, int(hm / gsd_m))
