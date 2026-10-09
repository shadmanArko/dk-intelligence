"""Plain-language reasons for the content scorer's top features (shared by the web pages and MCP tools)."""

from __future__ import annotations


def reason_text(feature: str) -> str:
    """'nominal__day_of_week_Saturday' -> 'Day: Saturday' (the champion's hour bands are in UTC)."""
    f = feature.split("__", 1)[-1]
    for prefix, label in (
        ("day_of_week_", "Day"),
        ("hour_bucket_", "Time band (UTC)"),
        ("platform_", "Platform"),
        ("content_type_filled_", "Format"),
        ("hashtag_band_", "Hashtags"),
    ):
        if f.startswith(prefix):
            return f"{label}: {f.removeprefix(prefix)}"
    return {
        "is_video_True": "Video",
        "is_video_False": "Not a video",
        "caption_length": "Caption length",
        "hashtag_count": "Number of hashtags",
        "first_line_length": "First line length",
        "script_bengali": "Bengali script",
    }.get(f, f.replace("_", " "))
