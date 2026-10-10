-- The RAG store (architecture §4.0, §4.5). Additive only (ADR 0015). Run by the builder role; the extension is
-- created once by the superuser (`intel pack init` on the Mac; migration 0007 on the VPS in P4.5).
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
    -- no fixed dimension: exact scan only (~16k rows), so a new embedding model is a new pack, not a migration
    embedding     vector not null,
    primary key (pack_id, cite)
);
create index if not exists chunk_tsv on rag.chunk using gin (tsv);
create index if not exists chunk_filters on rag.chunk (pack_id, collection, district, posted_at);
