# Contracts between the projects

Every link between dk-intelligence, dhaka-kacchi-reels and dk-publishing is listed in `registry.yaml`, with a reviewed
copy in `pinned/` (ADR 0033). The owner of a contract may **add** fields; renaming or removing one needs a new version.

```bash
uv run intel contracts sync --check   # did an owner change a contract? (exit 1 if so; writes nothing)
uv run intel contracts sync           # refresh the pinned copies, then:
uv run pytest tests/test_contracts.py # does our code still match? review the diff and commit both together
```

When you change a project:
- **reels:** after changing `src/jobs/spec.ts` or `src/jobs/manifest.ts`, run `npm run job:schema`. A breaking change
  bumps `CONTENT_SPEC_VERSION` or `MANIFEST_VERSION` in `src/jobs/manifest.ts`.
- **dk-publishing:** a request or response model change shows in `/openapi.json`; a platform rule change shows in
  `/v1/spec` (new `spec_version`, same `publishing-spec/v1`).
- **then here:** `intel contracts sync`, the tests, and the code the change needs.
