-- P4.7 (ADR 0030): the knowledge base for intel-mcp, in intel-db. Additive (ADR 0015). The same tables as the Mac's
-- src/intel/rag/schema.sql (a test keeps their columns equal). Run by the migration runner as `intel` (superuser
-- here), so `create extension` works. Two roles, created WITHOUT passwords (no secret in committed SQL): deploy.sh
-- sets them from deploy/.env on every deploy.
--   intel_mcp_reader  SELECT on schema rag only, read-only sessions        (intel-mcp's searches)
--   intel_mcp_loader  SELECT/INSERT/UPDATE/DELETE on rag tables, no DDL     (intel-mcp's pack sync)
-- Neither can read or write anything outside schema rag.
create extension if not exists vector;
create schema if not exists rag;

create table if not exists rag.pack (
    pack_id     text primary key,
    manifest    jsonb not null,
    loaded_at   timestamptz not null default now(),
    status      text not null check (status in ('staged', 'active', 'retired'))
);
create unique index if not exists one_active_pack on rag.pack ((true)) where status = 'active';

create table if not exists rag.chunk (
    pack_id       text not null references rag.pack on delete cascade,
    cite          text not null,
    collection    text not null check (collection in ('brand', 'rules', 'reviews', 'own_posts', 'creator_posts')),
    source_id     text not null,
    text          text not null,
    title         text,
    lang          text,
    posted_at     timestamptz,
    platform      text,
    district      text,
    area          text,
    listing_id    text,
    listing_name  text,
    rating_norm   numeric(5, 4),
    aspects       text[] not null default '{}',
    suspicious    boolean not null default false,
    dup_count     int not null default 1,
    tsv           tsvector not null,
    embedding     vector not null,
    content_type  text,
    account       text,
    url           text,
    metrics       jsonb,
    primary key (pack_id, cite)
);
create index if not exists chunk_tsv on rag.chunk using gin (tsv);
create index if not exists chunk_filters on rag.chunk (pack_id, collection, district, posted_at);
create index if not exists chunk_post_filters on rag.chunk (pack_id, collection, platform, content_type);

do $$
begin
    if not exists (select from pg_roles where rolname = 'intel_mcp_reader') then
        create role intel_mcp_reader login;
    end if;
    if not exists (select from pg_roles where rolname = 'intel_mcp_loader') then
        create role intel_mcp_loader login;
    end if;
end $$;
grant usage on schema rag to intel_mcp_reader, intel_mcp_loader;
grant select on rag.pack, rag.chunk to intel_mcp_reader;
grant select, insert, update, delete on rag.pack, rag.chunk to intel_mcp_loader;
alter role intel_mcp_reader set default_transaction_read_only = on;
alter role intel_mcp_reader set statement_timeout = '5s';
alter role intel_mcp_loader set statement_timeout = '600s';
