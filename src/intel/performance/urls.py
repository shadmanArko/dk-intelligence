"""One key for one post, whichever way a platform writes its address: the publisher's `live_url` and the warehouse's
`permalink` name the same post differently (a reel is /reel/<code>/ in one and /p/<code>/ in another), so both are
reduced to (platform, id)."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

IG = re.compile(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")
THREADS = re.compile(r"/post/([A-Za-z0-9_-]+)")
YT_PATH = re.compile(r"/(?:shorts|embed|live)/([A-Za-z0-9_-]{6,})")
FB_DIGITS = re.compile(r"/(\d{6,})")


def post_key_of(url: str | None) -> tuple[str, str] | None:
    """(platform, id) for an address, or None when it is not one we recognise."""
    if not url:
        return None
    u = urlparse(url.strip())
    host = u.netloc.lower().removeprefix("www.").removeprefix("m.")
    path = u.path
    if host.endswith("instagram.com") and (m := IG.search(path)):
        return "instagram", m.group(1)
    if host.startswith("threads.") and (m := THREADS.search(path)):
        return "threads", m.group(1)
    if host == "youtu.be" and path.strip("/"):
        return "youtube", path.strip("/").split("/")[0]
    if host.endswith("youtube.com"):
        if v := parse_qs(u.query).get("v"):
            return "youtube", v[0]
        if m := YT_PATH.search(path):
            return "youtube", m.group(1)
    if host.endswith("facebook.com") or host == "fb.watch":
        q = parse_qs(u.query)
        for key in ("story_fbid", "fbid", "v"):
            if q.get(key):
                return "facebook", q[key][0]
        ids = FB_DIGITS.findall(path)
        if ids:
            return "facebook", ids[-1]  # /<page>/posts/<id>, /reel/<id>/, /videos/<id>: the last number is the post
    return None
