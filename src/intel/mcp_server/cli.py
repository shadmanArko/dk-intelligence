"""`intel mcp …` and `intel pack …` (architecture §3.6). FastMCP is imported inside the commands that need it."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

import typer

mcp_app = typer.Typer(no_args_is_help=True, help="The dk_intel MCP server (read-only; ADR 0025)")
pack_app = typer.Typer(no_args_is_help=True, help="Knowledge packs the MCP servers read")

Profile = Annotated[str, typer.Option(help="owner, analyst, planner, writer, producer or checker")]


def _gateway(profile: str, server: str | None = None) -> Any:
    from intel.mcp_server.deps import Deps
    from intel.mcp_server.gateway import build_gateway
    from intel.mcp_server.kit.middleware import stdio_principal
    from intel.settings import McpSettings

    return build_gateway(profile, Deps.from_settings(McpSettings(), stdio_principal()), only=server)


@mcp_app.command("serve")
def serve(
    profile: Profile = "owner",
    server: Annotated[str | None, typer.Option(help="run one server alone: models or brand")] = None,
) -> None:
    """Serve over stdio (what Claude Code and Claude Desktop start). Prints nothing to stdout itself."""
    _gateway(profile, server).run(transport="stdio", show_banner=False)


@mcp_app.command("tools")
def tools(profile: Profile = "owner") -> None:
    """List the tools a profile sees, with an estimate of their size in tokens (bytes / 3)."""
    from fastmcp import Client

    async def go() -> None:
        async with Client(_gateway(profile)) as c:
            listed = await c.list_tools()
            total = 0
            for t in listed:
                size = len(json.dumps(t.model_dump(mode="json", exclude_none=True)))
                total += size
                typer.echo(f"{t.name:<24} ~{size // 3:>5} tokens")
            typer.echo(f"{len(listed)} tools, ~{total // 3} tokens for the whole list")

    asyncio.run(go())


@mcp_app.command("call")
def call(
    tool: str,
    arguments: Annotated[str, typer.Argument(help="JSON object")] = "{}",
    profile: Profile = "owner",
) -> None:
    """Call one tool in memory, as a client would (debugging)."""
    from fastmcp import Client

    async def go() -> None:
        async with Client(_gateway(profile)) as c:
            r = await c.call_tool(tool, json.loads(arguments), raise_on_error=False)
            if r.is_error:
                typer.echo(" ".join(getattr(b, "text", "") for b in r.content), err=True)
                raise typer.Exit(1)
            typer.echo(json.dumps(r.structured_content, indent=2, ensure_ascii=False, default=str))

    asyncio.run(go())


@mcp_app.command("audit")
def audit(action: Annotated[str, typer.Argument(help="verify | tail")] = "verify") -> None:
    """Check the audit hash chain, or show its last entries."""
    from intel.mcp_server.kit.audit import AuditLog
    from intel.settings import McpSettings

    log = AuditLog(McpSettings().audit_dir)
    if action == "verify":
        result = log.verify()
        typer.echo(json.dumps(result))
        raise typer.Exit(0 if result["ok"] else 1)
    for e in list(log.entries())[-20:]:
        typer.echo(
            f"{e['at']}  {e.get('status', ''):<7} {e.get('kind', ''):<9} {e.get('name', '')}  {e.get('ms', '')} ms"
        )


@pack_app.command("build")
def pack_build(
    index: Annotated[bool, typer.Option("--index/--brand-only", help="include the search index")] = False,
    embedder: Annotated[str | None, typer.Option(help="embedding model key (default INTEL_RAG_EMBEDDER)")] = None,
    activate: Annotated[bool, typer.Option(help="make it the active pack")] = True,
) -> None:
    """Build a new, immutable knowledge pack from the data platform's brand files, the latest market snapshot and
    the latest pull; with --index also the search index (chunks + vectors) for the brand, rules, reviews, own_posts
    and creator_posts collections."""
    from intel.rag import pack as kp
    from intel.settings import McpSettings

    s = McpSettings()
    indexer = None
    if index:
        from intel.rag.build import make_indexer
        from intel.rag.embed import load

        key = embedder or s.intel_rag_embedder
        if not key:
            raise typer.BadParameter("no embedder: pass --embedder or set INTEL_RAG_EMBEDDER")
        pulls = s.data_dir / "pulls"
        pull = pulls / (pulls / "LATEST").read_text().strip() if (pulls / "LATEST").exists() else None
        indexer = make_indexer(load(key), s.pack_dir / "cache", pull)
    pack_id = kp.build_brand_pack(s.path(s.mip_dir), s.data_dir / "market", s.pack_dir, indexer=indexer)
    if activate:
        kp.activate(s.pack_dir, pack_id)
    typer.echo(f"{pack_id} built{' and active' if activate else ''}")


@pack_app.command("load")
def pack_load(
    pack_id: Annotated[str | None, typer.Argument(help="default: the active pack")] = None,
    activate: Annotated[bool, typer.Option(help="make it the database's active pack if the gate passes")] = True,
    skip_gate: Annotated[bool, typer.Option("--skip-gate", help="activate without the gate (recorded)")] = False,
) -> None:
    """Load a pack's index into the knowledge database (INTEL_RAG_ADMIN_DSN, the builder role) as staged, run the
    activation gate against the active pack, and activate it only if the gate passes."""
    import numpy as np
    import pandas as pd
    import psycopg

    from intel.rag import pack as kp
    from intel.rag import store
    from intel.settings import McpSettings

    s = McpSettings()
    pid = pack_id or kp.active_id(s.pack_dir)
    if pid is None or not s.intel_rag_admin_dsn:
        raise typer.BadParameter("need a pack id (or an active pack) and INTEL_RAG_ADMIN_DSN (run `intel pack init`)")
    manifest = kp.verify(s.pack_dir / pid)
    if manifest.get("kind") != "full":
        raise typer.BadParameter(f"{pid} has no search index; build with --index")
    rows = pd.read_parquet(s.pack_dir / pid / "index" / "chunks.parquet")
    rows = rows.astype(object).where(rows.notna(), None)  # parquet NaN/NaT -> SQL NULL
    records = rows.to_dict("records")
    for r in records:
        r["aspects"] = list(r["aspects"]) if r.get("aspects") is not None else []
    vectors = np.load(s.pack_dir / pid / "index" / "vectors.npy")
    with psycopg.connect(s.intel_rag_admin_dsn, autocommit=True) as conn:
        store.ensure_schema(conn)
        n = store.load_pack(conn, pid, manifest, [{str(k): v for k, v in r.items()} for r in records], vectors)
        typer.echo(f"{pid}: {n} chunks loaded (staged)")
        if not activate:
            return
        result = None if skip_gate else _run_gate(conn, pid, manifest, s)
        if result is not None:
            out = s.pack_dir / "gates" / f"{pid}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result.as_dict(), indent=2))
            typer.echo(json.dumps(result.as_dict(), indent=2))
            if not result.passed:
                typer.secho(f"gate FAILED: {pid} stays staged; the active pack is unchanged", fg="red")
                raise typer.Exit(1)
        store.activate(conn, pid)
    kp.activate(s.pack_dir, pid)  # the brand and trends side reads the same pack as search
    typer.secho(f"{pid} is active{' (gate skipped)' if skip_gate else ' (gate passed)'}", fg="green")


def _run_gate(conn: Any, pid: str, manifest: dict[str, Any], s: Any) -> Any:
    """Search the golden dev questions in both packs with the serving stack (each pack with its own embedder)."""
    from intel.rag import gate, store
    from intel.rag.embed import load
    from intel.rag.rerank import load_reranker

    active = store.active_pack(conn)
    if active == pid:
        active = None
    manifests = {pid: manifest}
    if active is not None:
        row = conn.execute("select manifest from rag.pack where pack_id = %s", (active,)).fetchone()
        manifests[active] = row[0]
    embedders: dict[str, Any] = {}
    rr = load_reranker(s.intel_rag_reranker) if s.intel_rag_reranker else None
    reranker = (lambda q, docs: rr([(q, d) for d in docs])) if rr is not None else None

    def search(pack_id: str, q: dict[str, Any]) -> list[str]:
        name = manifests[pack_id]["embedder"]["name"]
        emb = embedders.setdefault(name, load(name))
        collection = ("own_posts", "creator_posts") if q["collection"] == "posts" else q["collection"]
        hits = store.search(
            conn, pack_id, collection, q["question"], emb.query(q["question"]), store.Filters(**q.get("filters", {})),
            s.intel_rag_mode, reranker, limit=gate.K, rerank_pool=s.intel_rag_rerank_pool,
        )  # fmt: skip
        return [h.cite for h in hits]

    return gate.evaluate(
        search, pid, active, manifest, gate.read_jsonl(gate.GOLDEN), gate.relevant(gate.read_jsonl(gate.QRELS))
    )


@pack_app.command("init")
def pack_init(
    start: Annotated[bool, typer.Option("--start", help="only (re)start an existing container")] = False,
) -> None:
    """Create the local knowledge database once: a `dk-rag` pgvector container on 127.0.0.1:55433, the roles
    rag_builder (loads packs) and rag_reader (SELECT only), and their DSNs in .env. Prints key names, never values."""
    import secrets
    import subprocess
    import time

    import psycopg
    from psycopg import sql

    from intel.settings import repo_root

    name, image, port = "dk-rag", "mirror.gcr.io/pgvector/pgvector:pg16", 55433
    exists = subprocess.run(["docker", "inspect", name], capture_output=True).returncode == 0
    if start or exists:
        subprocess.run(["docker", "start", name], check=True, capture_output=True)
        typer.echo(f"{name} is running on 127.0.0.1:{port}")
        if exists:
            return
    admin_pw, builder_pw, reader_pw = (secrets.token_urlsafe(24) for _ in range(3))
    subprocess.run(
        ["docker", "run", "-d", "--name", name, "--restart", "unless-stopped", "-p", f"127.0.0.1:{port}:5432",
         "-v", "dk-rag-data:/var/lib/postgresql/data", "-e", f"POSTGRES_PASSWORD={admin_pw}", "-e", "POSTGRES_DB=rag",
         image],
        check=True, capture_output=True,
    )  # fmt: skip
    admin = f"postgresql://postgres:{admin_pw}@127.0.0.1:{port}/rag"
    for _ in range(60):
        try:
            with psycopg.connect(admin, connect_timeout=2) as c:
                c.execute("select 1")
            break
        except psycopg.OperationalError:
            time.sleep(1)
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute("create extension if not exists vector")
        c.execute(sql.SQL("create role rag_builder login password {}").format(sql.Literal(builder_pw)))
        c.execute(sql.SQL("create role rag_reader login password {}").format(sql.Literal(reader_pw)))
        c.execute("create schema if not exists rag authorization rag_builder")
        c.execute("grant usage on schema rag to rag_reader")
        c.execute("alter default privileges for role rag_builder in schema rag grant select on tables to rag_reader")
        c.execute("alter role rag_reader set default_transaction_read_only = on")
    env = repo_root() / ".env"
    lines = [ln for ln in (env.read_text().splitlines() if env.exists() else [])
             if not ln.startswith(("INTEL_RAG_DSN=", "INTEL_RAG_ADMIN_DSN="))]  # fmt: skip
    lines += [
        f"INTEL_RAG_ADMIN_DSN=postgresql://rag_builder:{builder_pw}@127.0.0.1:{port}/rag",
        f"INTEL_RAG_DSN=postgresql://rag_reader:{reader_pw}@127.0.0.1:{port}/rag",
    ]
    env.write_text("\n".join(lines) + "\n")
    env.chmod(0o600)
    typer.echo(f"{name} created; INTEL_RAG_ADMIN_DSN and INTEL_RAG_DSN written to .env (values not shown)")


@pack_app.command("status")
def pack_status() -> None:
    from intel.rag import pack as kp
    from intel.settings import McpSettings

    typer.echo(json.dumps(kp.status(McpSettings().pack_dir), indent=2))


@pack_app.command("activate")
def pack_activate(pack_id: str) -> None:
    from intel.rag import pack as kp
    from intel.settings import McpSettings

    kp.activate(McpSettings().pack_dir, pack_id)
    typer.echo(f"{pack_id} is active")
