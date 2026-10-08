# Record of processing

<!-- Generated from src/intel/privacy/policy.yaml by `intel privacy ropa --write`. Do not edit by hand. -->

Policy version 1, updated 2026-10-09. Controller: Dhaka Kacchi Berlin (the owner, one person); this system is run by the owner for the owner.

## Purposes
- Decide what to post, when and where (content and reach scorers, occasion planner, creator matcher).
- Understand the market and the business (competitor weakness miner, community map, funnel).
- Run the service safely (monitoring, audit trail, backups).

No automated decision with legal or similarly significant effect on a person is made: every model advises the
owner, who decides. No data is sold or shared; processors are the VPS host and Backblaze B2 (EU) for backups.

## Warehouse snapshots (only these tables leave the VPS)
Stored: Mac, data/pulls/<id>/ (Parquet, checksummed); the newest 12 pulls are kept (INTEL_KEEP_PULLS).

| Table | Purpose | Personal data | Categories / notes | Retention |
|---|---|---|---|---|
| `social_post` | training data for content_scorer / reach_scorer (our own posts and captions); funnel post links | no personal data | captions are our own public posts; the only phone number in them is the business's own (allow-listed) | newest 12 snapshots |
| `social_metrics_snapshot` | training labels (likes, comments, shares, reach of our own posts over time) | no personal data | - | newest 12 snapshots |
| `raw_orders_direct` | funnel (orders per day and source) and community_map (orders counted per postcode area) | pseudonymous (personal data) | order time, items, amounts, delivery city and postal code; no name, email, phone or street; postcodes are only counted per planning area (542 areas); never shown or exported per order | newest 12 snapshots |
| `raw_posthog_events` | funnel (website sessions, entry source, checkout steps) | pseudonymous (personal data) | random visitor and session ids (dk_...), page paths, coarse GeoIP (country, region); no IP address | newest 12 snapshots |
| `tracked_link` | funnel (which post or campaign a visit came from) | no personal data | - | newest 12 snapshots |

## Market snapshots (data platform, fixed queries)
Stored: Mac, data/market/<id>/ (Parquet, checksummed); the newest 12 pulls are kept.

| Table | Purpose | Personal data | Categories / notes | Retention |
|---|---|---|---|---|
| `content_feature_performance` | market lift per content tag (content_scorer features) | no personal data | - | newest 12 snapshots |
| `occasion_day` | occasion calendar (content_scorer features) | no personal data | - | newest 12 snapshots |
| `occasions_next` | occasion planner (the next occasions, with how many Berlin residents share each) | no personal data | derived from residents by origin / citizenship (see fairness) | newest 12 snapshots |
| `afs_citizenship` | occasion planner (how many Berlin residents share an occasion; city-wide counts) | no personal data | official aggregate statistics, never per person; derived from residents by origin / citizenship (see fairness) | newest 12 snapshots |
| `biryani_reviews` | weakness_miner (what customers of other biryani sellers complain about) | pseudonymous (personal data) | review text and rating; no reviewer name or id; emails and phone numbers redacted in text | newest 12 snapshots |
| `biryani_listings` | weakness_miner and community_map (competitor places) | no personal data | - | newest 12 snapshots |
| `lor_community` | community_map (residents per planning area by community of origin; official aggregates) | no personal data | reviewed exception for protected-attribute data, see fairness.exceptions; derived from residents by origin / citizenship (see fairness) | newest 12 snapshots |
| `lor_stats` | community_map (age | no personal data | - | newest 12 snapshots |
| `lor_places` | community_map (area centres | no personal data | - | newest 12 snapshots |
| `postcode_area` | community_map (postcode -> planning area) | no personal data | - | newest 12 snapshots |
| `creators` | creator_matcher (public Berlin food creators who could make paid content with us) | **declared personal data** | public handle, display name, bio, website, public post statistics; public business-facing profiles; used only to suggest whom the owner could contact | newest 12 snapshots |
| `creator_posts` | creator_matcher (what those creators post about) | **declared personal data** | public post captions, links and counts | newest 12 snapshots |

## Serving database (intel-db)
Stored: VPS, intel-db (Postgres, Docker volume); nightly backups kept 30 days locally and in Backblaze B2.

| Table | Purpose | Personal data | Categories / notes | Retention |
|---|---|---|---|---|
| `schema_migrations` | database schema version | no personal data | - | kept: needed to migrate |
| `model_package` | model registry | no personal data | - | kept: governance record (ADR 0019) |
| `approval` | who approved which model | no personal data | - | kept: governance record |
| `model_activation` | which model served when | no personal data | - | kept: append-only record |
| `audit_log` | hash-chained audit trail | no personal data | - | kept: append-only by design |
| `model_reference` | training reference profiles for drift | no personal data | - | kept: small |
| `kv` | settings and markers | no personal data | - | kept: current state only |
| `ledger_post` | our own posts scored by the models (caption hash and length only, never the caption) | no personal data | - | kept: the model ledger is the evidence the judge and guardrail rely on |
| `post_outcome` | real results of our posts | no personal data | - | kept: model ledger |
| `prediction` | every prediction (shadow scoring of our posts, and draft scoring through the API) | no personal data | API calls score drafts (features and an input hash, no caption text); 90 days covers drift windows | kept: shadow predictions are the model ledger; `source = 'api'` rows: 90 days |
| `event_log` | operational events (packages received, measure runs, start-ups) | no personal data | - | 180 days (by `at`) |
| `training_run` | reports of the Sunday training runs (for the training page and its history) | no personal data | - | 365 days (by `received_at`) |

## Files and copies
| What | Where | Retention | Enforced by |
|---|---|---|---|
| intel-db backups | VPS /opt/dhaka-kacchi/backups/intel_*.sql.gz | 30 days | deploy/backup.sh |
| backup copies | Backblaze B2 (EU) | 30 days | bucket lifecycle (harness) |
| VPS job logs | /opt/dhaka-kacchi/logs/intel-*.log, mip-collector.log | 56 days | logrotate (infra/site.yml) |
| container logs | Docker json-file (intel-api, intel-db) | 3 x 10 MB | deploy/docker-compose.yml |
| metrics | VPS Prometheus | 35 days | deploy/monitoring/docker-compose.yml |
| warehouse and market pulls | Mac data/pulls | newest 12 | intel.pull.prune |

## Protected attributes
Never used in a field name or produced: religion, religious, ethnicity, ethnic, race, racial, nationality, citizenship, citizen, country_of_origin, origin_country, caste, migration_background, skin, sexual, gender, disability, health, and the data
platform's communities of origin. Reviewed exceptions (aggregate statistics, never individuals):

| Model | Reads | Basis | Use | Reviewed | Review again |
|---|---|---|---|---|---|
| community_map | lor_community | official area statistics (residents per planning area by community of origin), never individuals | choosing AREAS for delivery zones, area-targeted ads and pop-ups; never to target or exclude people | owner, 2026-10-08 | 2027-04-08 |
| occasion_planner | afs_citizenship, occasions_next | official city-wide counts of residents by citizenship, used only to size an occasion's audience | which occasions to plan content for and when; presentation is neutral (content playbook) and never addresses or excludes people by origin or religion | owner, 2026-10-09 | 2027-04-09 |

## Safeguards
- A pull copies declared tables only; undeclared personal data fails the pull (counts, never values).
- Every package passes the responsible-AI gate (protected attributes by name and by lineage, blocked words in
  generated text, personal data) before it is built.
- Deletion runs daily in intel-api, in one transaction with its audit entry; a stalled job raises an alert.
- Identities: see [HASHING.md](HASHING.md) for what is hashed, with which salt, and what is never copied.
