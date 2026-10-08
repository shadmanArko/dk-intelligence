# 0013. Right-sized tooling: what we deliberately do not run yet

- **Status:** Accepted
- **Date:** 2026-10-08

## Context
Large-company MLOps stacks (Kubernetes, Kubeflow / Airflow, a feature store, a model-serving mesh) solve problems of
many teams, many models in parallel, and online features at high request rates. Here: one engineer, ~460 own posts,
eight models, a few requests per hour.

## Decision
We implement the **practices** (reproducible data, gated promotion, lineage, shadow evaluation, rollback, monitoring,
infrastructure as code) with the smallest tools that give the guarantee. Each heavier tool has a written trigger:

| Tool | Adopt when |
|---|---|
| Feature store (e.g. Feast) | more than one consumer needs the same online features, or features need sub-hour freshness |
| Workflow orchestrator (Airflow / Prefect / Dagster) | more than ~3 scheduled pipelines with cross-dependencies, or retries need a UI |
| Kubernetes | more than one host, or autoscaling is needed |
| Distributed training (Ray / Spark) | training data no longer fits in memory on one machine |
| Model-serving framework (KServe / BentoML) | several models with independent scaling or GPU serving |

The full scaling plan is in `docs/SCALING.md` (H9).

## Consequences
- Good: every component is understood end to end and runs on €0 extra.
- Cost: we must notice the triggers; H3/H9 make the relevant numbers visible.
