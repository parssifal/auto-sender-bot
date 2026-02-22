from __future__ import annotations

from timezonefinder import TimezoneFinder

_finder = TimezoneFinder()


def timezone_from_coordinates(latitude: float, longitude: float) -> str | None:
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        return None
    try:
        return _finder.timezone_at(lat=latitude, lng=longitude)
    except ValueError:
        return None
