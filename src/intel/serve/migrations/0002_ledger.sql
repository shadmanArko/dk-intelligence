-- M6 prediction ledger. Every FB/IG post published after the ledger start is scored ONCE per model ("shadow"),
-- using only what was known before posting (platform, format, time, caption); the real numbers are recorded later
-- at fixed horizons. API calls (/v1/predict) are logged too, without a post. Captions are not stored, only a hash.

create table ledger_post (
    post_id         uuid primary key,             -- warehouse social_post.id
    platform        text not null,
    posted_at       timestamptz not null,
    content_type    text,
    caption_sha256  text not null,
    caption_length  int not null,
    first_seen_at   timestamptz not null default now()
);

create table prediction (
    id               uuid primary key default gen_random_uuid(),
    source           text not null check (source in ('shadow', 'api')),
    model_name       text not null,               -- content_scorer | harness_predictor
    model_version    text not null,
    post_id          uuid references ledger_post(post_id),
    platform         text not null,
    planned_at       timestamptz not null,
    input_sha256     text not null,
    probability      double precision not null check (probability between 0 and 1),
    threshold        double precision not null,
    predicted_label  smallint not null check (predicted_label in (0, 1)),
    top_reasons      jsonb,
    created_at       timestamptz not null default now(),
    check (source = 'api' or post_id is not null)
);
create unique index prediction_one_shadow_per_post_model on prediction (post_id, model_name) where source = 'shadow';
create index prediction_created on prediction (created_at);

create table post_outcome (
    post_id          uuid not null references ledger_post(post_id),
    horizon          text not null check (horizon in ('24h', '72h', '7d')),
    captured_at      timestamptz not null,
    age_hours        numeric(7, 1) not null,       -- actual snapshot age; may be later than the horizon
    impressions      bigint, reach bigint, likes bigint, comments bigint, shares bigint, saves bigint, clicks bigint,
    engagement       bigint,                        -- likes + comments + shares (the training label's measure)
    platform_median  double precision,              -- median latest engagement of EARLIER posts, same platform
    label            smallint check (label in (0, 1)),
    recorded_at      timestamptz not null default now(),
    primary key (post_id, horizon)
);
