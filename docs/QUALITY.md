# Quality ratchets

Numbers that may only get better. Each row says where it is enforced; lowering a floor needs a reviewed change with a
reason (ADR 0010).

| Measure | 2026-10-08 (H0 start) | Now | Floor / rule | Enforced by |
|---|---|---|---|---|
| mypy errors (default checks) | 200 | **0** (+44 documented typing debt in sealed files) | 0 | pre-commit, CI |
| Modules passing `--strict` | 0 | **23** | list in `pyproject.toml` only grows | pre-commit, CI |
| Branch coverage (CI, authoritative) | 72% (line, Mac) | **82.9%** on the Mac (training now runs in CI too) | `fail_under = 80`, compared to 2 decimals | CI |
| Tests | 117 | **221** + 14 alert-rule test cases | all pass | CI |
| Secrets in git history | not scanned | **0** (both repos, full history) | 0 | CI (gitleaks) |
| Sealed files changed without a model change | not detectable | **0** | 0 | test + pre-commit |
| Fixable HIGH/CRITICAL vulnerabilities in the production image | 6 (never scanned) | **0** | 0 | CI (trivy, gates the deploy) |
| Known-vulnerable locked Python packages | 2 (pyarrow, pytest) | **0** | 0 | CI (pip-audit with hashes) |
| Production image size | 1.63 GB | **955 MB** | watched | CI summary |
| Unpinned GitHub Actions / tool images | all | **0** | 0 | review + Dependabot |
| Model input tables without a data contract | 17 of 17 | **0** | 0 (a new table needs a contract) | CI tests + Sunday gate |
| Production model reproducible from its lineage | never tested | **yes** (75/75 predictions identical) | CI test + `intel reproduce` | CI |
| Audit trail integrity | no audit trail | **hash chain verified** | `intel_audit_chain_ok == 1` | alert (page) |
| Alerts without a runbook section | n/a | **0** | 0 | CI test |
| Newest backup restored and verified | never | **weekly** (drill) | ≤ 8 days | alert |
| Alert rules without a unit test | n/a (no alerts) | **0 of the SLO / paging alerts** | every paging alert tested | CI (promtool) |

\* The Mac also runs the harness-reproduction test (it needs the private harness repo next to this one), which
covers `champions/reproduce.py`; CI skips it. The floor is set against the CI number.

**Lesson recorded:** with coverage's default `precision = 0` the total is rounded before the comparison, so 75.99%
passed a 76% floor while pytest-cov printed "FAIL" (first H0 CI run). `precision = 2` makes the gate exact.

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
