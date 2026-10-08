# Hashing rules

A hash is only a privacy measure when nobody can guess the input. An email address or phone number hashed **without
a secret salt** can be reversed by hashing every likely value and comparing. So the rule is:

> **Identities of people are either not copied at all, or hashed with a secret salt (HMAC). Unsalted SHA-256 is used
> only for things that are not about a person: our own captions, model files, data files.**

| Hash | Of what | Salted? | Why that is right | Where |
|---|---|---|---|---|
| `caption_sha256` | our own post captions | no | the caption is our public post, not a person; the hash lets the ledger recognise a post without storing its text | intel-db `ledger_post` |
| `input_sha256` | a prediction's inputs (platform, format, time, caption) | no | same: our own drafts; used to spot repeats, never to identify anyone | intel-db `prediction` |
| package, pull and file checksums | model packages, Parquet files, manifests | no | integrity, not privacy (ADR 0004, ADR 0011) | `SHA256SUMS`, `*.sha256` |
| `allow_phone_sha256` | the business's own public phone numbers | no | the number is public; storing its hash keeps it out of the repository text while the PII scan still recognises it | `src/intel/privacy/policy.yaml` |
| reviewer / commenter ids (data platform) | identities of reviewers and commenters on public platforms | **yes**: HMAC-SHA256 with `MIP_HASH_SALT`, namespaced per source | stays linkable inside the platform (same reviewer, same pseudonym), cannot be reversed without the salt; the platform refuses to store identities when the salt is missing | data platform `mip.core.privacy.pseudonym` |
| customer contacts (harness) | customers' emails / phones | **yes** (harness `contact_hashes`) | lets the harness recognise a returning customer; erased with the customer (`deleted_at` ⇒ empty hashes) | warehouse `customer` (never pulled here) |

## What is never copied into dk-intelligence
- Reviewer and commenter identities, not even as hashes (the market pull selects review text and rating only).
- Customers, their contact hashes and the harness's own customer reviews (`customer`, `review` are not pulled).
- Facebook comments and their authors (`raw_social_posts_*` are not pulled; pulls made before ADR 0024 are cleaned with
  `intel privacy minimise`).
- IP addresses: the website events carry a coarse GeoIP (country, region) and random visitor ids (`dk_...`), no IP.

## Pseudonymous is still personal
Random visitor ids, order postcodes and salted hashes are **pseudonymous personal data** under the GDPR: they are
declared as such in the [record of processing](RECORD_OF_PROCESSING.md), kept only as long as the snapshots that hold
them, and only ever shown in aggregate (per day, per source, per planning area).
