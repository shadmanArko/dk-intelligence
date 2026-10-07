-- M7: one row per weekly training run report uploaded by the Mac (through the upload-only inbox).
create table training_run (
    run_id        text primary key,               -- e.g. 20261011T100512Z
    status        text not null check (status in ('pass','fail','baseline','error')),
    failed_step   text,
    version       text,
    package       text,
    report        jsonb not null,
    file_sha256   text not null unique,
    received_at   timestamptz not null default now()
);
