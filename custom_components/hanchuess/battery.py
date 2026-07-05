"""Helpers for Hanchuess battery discovery."""


def extract_battery_serials(station_detail: dict) -> list[str]:
    """Return battery serials from a station detail payload."""
    serials = []
    for item in station_detail.get("data", {}).get("bmsList", []):
        sn = item.get("sn")
        if sn:
            serials.append(sn)
    return serials


def merge_battery_serials(existing_serials: list[str], station_detail: dict) -> list[str]:
    """Merge stored battery serials with the latest station detail payload."""
    merged = []
    seen = set()
    for serial in [*existing_serials, *extract_battery_serials(station_detail)]:
        if serial and serial not in seen:
            seen.add(serial)
            merged.append(serial)
    return merged
