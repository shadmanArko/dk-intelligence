-- Model registry, approvals and activation history for intel-api. Activation history is append-only: the active
-- model of a name is its latest activation; a rollback is a new activation of the previous package.

create table model_package (
    id           bigserial primary key,
    name         text not null,
    version      text not null,
    file_name    text not null,
    sha256       text not null unique,          -- outer archive checksum; one row per distinct upload
    purpose      text not null,                 -- candidate | baseline | pipeline-test | unknown
    verdict      text not null,
    status       text not null check (status in
                   ('invalid','incompatible','test_only','pending_approval','approved','rejected','expired')),
    problems     text[] not null default '{}',
    metadata     jsonb not null default '{}',
    store_path   text,
    received_at  timestamptz not null default now(),
    unique (name, version)
);

create table approval (
    id                  bigserial primary key,
    package_id          bigint not null references model_package(id),
    requested_at        timestamptz not null default now(),
    expires_at          timestamptz not null,
    decision            text check (decision in ('approved','rejected','expired')),
    decided_at          timestamptz,
    decided_by          text,
    channel             text check (channel in ('telegram','web','system')),
    reason_code         text,
    telegram_message_id bigint
);
create unique index approval_one_open_per_package on approval (package_id) where decision is null;

create table model_activation (
    id            bigserial primary key,
    name          text not null,
    package_id    bigint not null references model_package(id),
    activated_at  timestamptz not null default now(),
    activated_by  text not null,
    reason        text not null check (reason in ('approval','rollback'))
);
create index model_activation_name on model_activation (name, id desc);

create table event_log (
    id      bigserial primary key,
    at      timestamptz not null default now(),
    kind    text not null,
    detail  jsonb not null default '{}'
);

create table kv (
    key    text primary key,
    value  jsonb not null
);
