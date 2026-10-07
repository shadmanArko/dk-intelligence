# M8 — Admin post predictor switched to dk-intelligence (2026-10-07)

**Decision (owner):** switch now, so every future approved model reaches `/admin/post-predict` without another change.

## What changed
- **Harness `3c0e1e4` (on main, deployed):** `ordering-backend`'s `PREDICTOR_URL` reads `${PREDICTOR_URL:-http://predictor:8000}`.
  The default is unchanged.
- **VPS `dhaka_kacchi_ai_harness/deploy/.env`:** `PREDICTOR_URL=http://intel-api:8000`, then
  `docker compose up -d ordering-backend`.
- **The old `predictor` service keeps running.** intel-api scores every real post with it too (live comparison
  in the ledger).

## Verified
- `ordering-backend` → `http://intel-api:8000/ready`: ready, version `df96ac3`, model `cs-base-20261007T1743-33eb38`.
- `printenv PREDICTOR_URL` inside `ordering-backend`: `http://intel-api:8000`.
- **Admin page:** a draft (Facebook, video, Wed 22:46, "Hello") gave "Likely at or above typical", 63%, model
  `cs-base-20261007T1743-33eb38`, with reasons. The intel-api API-prediction count went from 0 to 1.

## Switch back
Remove the `PREDICTOR_URL=` line from the harness `deploy/.env`, then `docker compose up -d ordering-backend`.

## Notes
- The admin page text still says "model trained on <400 of our own past posts … ml/03-modeling/report.md". That is
  still roughly true for the baseline, but it should eventually link to the model card of the active model.
- The champion features use UTC hours ("hour bucket"); 22:46 Berlin = 20:46 UTC → "evening".
