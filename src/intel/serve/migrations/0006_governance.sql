-- H5 governance (ADR 0019). Additive only (ADR 0015): new table, functions and triggers; no column changes.
--
-- 1. audit_log: every governance action, written in the SAME transaction as the change it records. Append-only
--    (UPDATE / DELETE / TRUNCATE are refused) and hash-chained: each row's hash covers the previous row's hash, so
--    editing or removing any entry breaks the chain from that point on (audit_verify() finds it).
-- 2. The registry's rules, enforced by the database itself, so no code path (or manual SQL) can bypass them:
--    a package's status moves only along the lifecycle; an approval decision is set once; activation history is
--    append-only.

create table audit_log (
    id         bigserial primary key,
    at         timestamptz not null default now(),
    actor      text not null,             -- Telegram user, Tailscale login, or 'system'
    channel    text not null,             -- telegram | web | system | inbox | migration
    action     text not null,             -- package_received, approval_requested, approved, rejected, ...
    subject    text not null,             -- model name / version (or another stable id)
    detail     jsonb not null default '{}',
    prev_hash  text not null,
    hash       text not null unique
);

create function audit_row_hash(prev text, at timestamptz, actor text, channel text, action text, subject text,
                               detail jsonb) returns text language sql immutable as $$
    select encode(sha256(convert_to(concat_ws('|', prev,
        (extract(epoch from at) * 1000000)::bigint::text,  -- microseconds since epoch: no time-zone dependence
        actor, channel, action, subject, detail::text), 'UTF8')), 'hex')
$$;

create function audit_chain() returns trigger language plpgsql as $$
declare last text;
begin
    perform pg_advisory_xact_lock(4243);             -- one writer at a time: a strict chain
    select hash into last from audit_log order by id desc limit 1;
    new.prev_hash := coalesce(last, repeat('0', 64));
    new.hash := audit_row_hash(new.prev_hash, new.at, new.actor, new.channel, new.action, new.subject, new.detail);
    return new;
end $$;

create trigger audit_chain before insert on audit_log for each row execute function audit_chain();

create function refuse_change() returns trigger language plpgsql as $$
begin
    raise exception '% is append-only (% refused)', tg_table_name, tg_op using errcode = 'insufficient_privilege';
end $$;

create trigger audit_append_only before update or delete on audit_log for each row execute function refuse_change();
create trigger audit_no_truncate before truncate on audit_log for each statement execute function refuse_change();

-- First row that does not verify (NULL = the whole chain is intact).
create function audit_verify() returns bigint language sql stable as $$
    select min(id) from (
        select id, hash, prev_hash, lag(hash) over (order by id) as expected_prev,
               audit_row_hash(prev_hash, at, actor, channel, action, subject, detail) as recomputed
        from audit_log
    ) x
    where hash <> recomputed or prev_hash <> coalesce(expected_prev, repeat('0', 64))
$$;

-- ---------- lifecycle of a package, enforced (mirrors intel/serve/lifecycle.py; a test keeps them equal) ----------
create function package_status_rule() returns trigger language plpgsql as $$
begin
    if (old.status, new.status) not in (('pending_approval', 'approved'), ('pending_approval', 'rejected'),
                                        ('pending_approval', 'expired')) then
        raise exception 'illegal lifecycle transition % -> % for package %', old.status, new.status, old.id
            using errcode = 'check_violation';
    end if;
    return new;
end $$;

create trigger package_status_rule before update of status on model_package
    for each row when (old.status is distinct from new.status) execute function package_status_rule();

create function approval_decided_once() returns trigger language plpgsql as $$
begin
    if old.decision is not null and (new.decision is distinct from old.decision or new.decided_by is distinct from
                                     old.decided_by or new.decided_at is distinct from old.decided_at) then
        raise exception 'approval % was already decided (%): decisions are final', old.id, old.decision
            using errcode = 'check_violation';
    end if;
    return new;
end $$;

create trigger approval_decided_once before update on approval for each row execute function approval_decided_once();

create trigger activation_append_only before update or delete on model_activation
    for each row execute function refuse_change();

-- ---------- backfill: the history so far becomes the start of the chain (marked as backfilled) ----------
insert into audit_log (at, actor, channel, action, subject, detail)
select at, actor, channel, action, subject, detail from (
    select p.received_at as at, 'system' as actor, 'inbox' as channel, 'package_received' as action,
           p.name || ' ' || p.version as subject,
           jsonb_build_object('status', p.status, 'purpose', p.purpose, 'verdict', p.verdict, 'backfilled', true) as detail,
           1 as ord, p.id as k
      from model_package p
    union all
    select a.decided_at, coalesce(a.decided_by, 'system'), coalesce(a.channel, 'system'), a.decision,
           p.name || ' ' || p.version,
           jsonb_build_object('approval_id', a.id, 'reason_code', a.reason_code, 'backfilled', true), 2, a.id
      from approval a join model_package p on p.id = a.package_id
     where a.decision is not null
    union all
    select m.activated_at, m.activated_by, 'system', case when m.reason = 'rollback' then 'rolled_back' else 'activated' end,
           p.name || ' ' || p.version, jsonb_build_object('activation_id', m.id, 'backfilled', true), 3, m.id
      from model_activation m join model_package p on p.id = m.package_id
) h
order by at, ord, k;
