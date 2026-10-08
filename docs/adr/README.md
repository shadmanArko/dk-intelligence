# Architecture Decision Records

One page per decision that shapes the system: why it was taken, what it costs, what was rejected. Records are never
edited after acceptance; a changed decision gets a new record that **supersedes** the old one.

| # | Decision | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-train-on-the-mac-serve-on-the-vps.md) | Train on the Mac, serve on the VPS | Accepted |
| [0003](0003-three-narrow-doors.md) | Three narrow doors; no shell for the assistant | Accepted |
| [0004](0004-immutable-checksummed-snapshots.md) | Immutable, checksummed data snapshots | Accepted |
| [0005](0005-judge-gates-and-human-approval.md) | Champion vs challenger: fixed gates, then a human | Accepted |
| [0006](0006-shadow-ledger.md) | Shadow ledger: predictions registered before outcomes exist | Accepted |
| [0007](0007-packages-pin-their-feature-code.md) | Packages pin their feature code; the server refuses a mismatch | Accepted |
| [0008](0008-insights-auto-refresh.md) | Insights refresh automatically after a first approval; scorers never | Accepted |
| [0009](0009-model-plugin-interface.md) | One plug-in interface for every model | Accepted |
| [0010](0010-gradual-typing-and-quality-ratchets.md) | Gradual typing and quality ratchets | Accepted |
| [0011](0011-sealed-model-code.md) | Sealed model code changes only with the model | Accepted |
| [0012](0012-local-models-zero-running-cost.md) | Local open models; €0 running cost | Accepted |
| [0013](0013-right-sized-tooling.md) | Right-sized tooling: what we deliberately do not run yet | Accepted |
| [0014](0014-build-once-deploy-by-digest.md) | Build once in CI, deploy by digest, roll back automatically | Accepted |
| [0015](0015-additive-migrations.md) | intel-db migrations are additive (expand / contract) | Accepted |
| [0016](0016-observability-and-slos.md) | Observability: metrics, probes, SLOs and alerts as code | Accepted |

Template: [0000-template.md](0000-template.md).
