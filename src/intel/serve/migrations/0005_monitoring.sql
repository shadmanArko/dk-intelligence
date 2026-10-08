-- H4 model monitoring (ADR 0018). Additive only (ADR 0015).
-- The model's input features exactly as served (shadow scoring): the live side of drift and of the training/serving
-- skew check. NULL for older predictions and for the production predictor (computed elsewhere).
alter table prediction add column features jsonb;

-- Reference profile per model version, built on the Mac from the training run and delivered through the inbox:
-- feature distributions of the training set, score distribution on the test set, base rate, test metrics.
create table model_reference (
    model_name     text not null,
    model_version  text not null,
    payload        jsonb not null,
    received_at    timestamptz not null default now(),
    primary key (model_name, model_version)
);
