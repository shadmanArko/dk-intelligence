# Quality ratchets

Numbers that may only get better. Each row says where it is enforced; lowering a floor needs a reviewed change with a
reason (ADR 0010).

| Measure | 2026-10-08 (H0 start) | Now | Floor / rule | Enforced by |
|---|---|---|---|---|
| mypy errors (default checks) | 200 | **0** (+44 documented typing debt in sealed files) | 0 | pre-commit, CI |
| Modules passing `--strict` | 0 | **14** | list in `pyproject.toml` only grows | pre-commit, CI |
| Branch coverage | 72% (line) | **76.7%** (branch) | `fail_under = 76` | CI |
| Tests | 117 | **130** | all pass | CI |
| Secrets in git history | not scanned | **0** (both repos, full history) | 0 | CI (gitleaks) |
| Sealed files changed without a model change | not detectable | **0** | 0 | test + pre-commit |

## Typing debt (sealed model code, ADR 0011)

Fixed together with each model's next real change; remove the module from the debt list in `pyproject.toml` then.

| Module | Errors | Kind |
|---|---|---|
| `models/funnel/run.py` | 22 | pandas scalar types |
| `models/occasion_planner/run.py` | 9 | pandas scalars, untyped dicts |
| `models/community_map/run.py` | 7 | pandas Series vs scalar |
| `models/content_scorer/features.py` | 2 | pandas scalar → float |
| `models/weakness_miner/mine.py` | 2 | pandas scalar → str/float |
| `models/creator_matcher/run.py` | 1 | pandas scalar → int |
| `champions/harness_v1/features.py` | 1 | return annotation (vendored; never edited here) |
