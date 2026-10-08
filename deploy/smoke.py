"""Post-deploy smoke test, run INSIDE the new intel-api container by deploy.sh (ADR 0014). Exit 0 = keep it.

Checks, in order:
1. /ready answers within the wait window (database reachable, migrations applied, models loaded);
2. it reports the git commit that was deployed (the right image is running);
3. every approved model that was active before the deploy is loaded again (none silently lost; a version may move
   on if the inbox worker activated an approved package during the deploy window);
4. if a content_scorer is active: a real prediction (via /health/deep: nothing is written to the ledger) returns a
   probability in [0, 1].

Environment: EXPECT_SHA (full commit), EXPECT_MODELS (JSON object name -> version from the old /ready; empty when
the old container was not answering), WAIT_S.
Standard library only: it runs in the production image.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8000")  # overridable for the test
DRAFT = {
    "platform": "instagram",
    "content_type": "reel",
    "caption": "Smoke test: kacchi biryani, slow-cooked dum",
    "planned_posted_at": "2026-10-10T18:00:00+02:00",
}


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=5) as r:  # noqa: S310 (fixed local URL)
        return json.loads(r.read())


def request(path: str, body: dict | None = None) -> tuple[int, dict]:
    """GET (no body) or POST JSON; returns (status, JSON body), also for HTTP errors."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 (fixed local URL)
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {"detail": e.read().decode(errors="replace")[:200]}


def main() -> int:
    expect_sha = os.environ["EXPECT_SHA"]
    expect_models: dict[str, str] = json.loads(os.environ.get("EXPECT_MODELS") or "{}")
    deadline = time.monotonic() + int(os.environ.get("WAIT_S", "90"))
    ready, last_error = None, ""
    while time.monotonic() < deadline:
        try:
            ready = get("/ready")
            break
        except (OSError, ValueError) as e:
            last_error = f"{type(e).__name__}: {e}"
            time.sleep(3)
    if ready is None:
        print(f"FAIL /ready did not answer: {last_error}")
        return 1
    problems = []
    if ready.get("version") != expect_sha:
        problems.append(f"running version {ready.get('version')!r}, expected {expect_sha!r}")
    models = ready.get("models") or {}
    if lost := sorted(set(expect_models) - set(models)):
        problems.append(f"models active before the deploy are not loaded: {lost} (now {models})")
    moved = {n: (expect_models[n], v) for n, v in models.items() if n in expect_models and expect_models[n] != v}
    if "content_scorer" in models:
        # /health/deep predicts without writing to the ledger; images from before 2026-10-08 (a rollback target) lack
        # it, so they are checked through /v1/predict as before.
        code, deep = request("/health/deep")
        if code == 404:
            code, body = request("/v1/predict", DRAFT)
            p = body.get("probability")
        else:
            p = (deep.get("prediction") or {}).get("probability")
        if code != 200 or not isinstance(p, int | float) or not 0 <= p <= 1:
            problems.append(f"prediction check returned {code}, probability {p!r}")
    for problem in problems:
        print(f"FAIL {problem}")
    if not problems:
        note = f" (activated during the deploy: {moved})" if moved else ""
        print(f"OK version {expect_sha[:10]}, models {models}{note}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
