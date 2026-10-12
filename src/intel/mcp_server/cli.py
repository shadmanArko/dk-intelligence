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
    transport: Annotated[
        str, typer.Option(help="stdio (one client) or http (every profile at /mcp/<profile>)")
    ] = "stdio",
    listener: Annotated[str, typer.Option(help="http only: local, tailnet or both")] = "local",
) -> None:
    """Serve over stdio (what Claude Code and Claude Desktop start; prints nothing to stdout itself) or over
    Streamable HTTP on 127.0.0.1 with a JWT per call (ADR 0029)."""
    if transport == "stdio":
        _gateway(profile, server).run(transport="stdio", show_banner=False)
        return
    if transport != "http" or listener not in ("local", "tailnet", "both"):
        raise typer.BadParameter("--transport stdio|http, --listener local|tailnet|both")
    from intel.mcp_server.deps import Deps
    from intel.mcp_server.http import serve as serve_http
    from intel.settings import McpSettings

    s = McpSettings()
    chosen: list[Any] = ["local", "tailnet"] if listener == "both" else [listener]
    ports = {"local": s.intel_mcp_http_port, "tailnet": s.intel_mcp_tailnet_port}
    typer.echo(", ".join(f"{name} http://127.0.0.1:{ports[name]}/mcp/<profile>" for name in chosen), err=True)
    serve_http(s, Deps.from_settings(s, f"http:{s.intel_mcp_host}"), chosen)


def _write_env(key: str, value: str) -> None:
    """Set KEY=value in the repo's .env (mode 600); the value is never printed."""
    from intel.settings import repo_root

    env = repo_root() / ".env"
    lines = [ln for ln in (env.read_text().splitlines() if env.exists() else []) if not ln.startswith(f"{key}=")]
    env.write_text("\n".join([*lines, f"{key}={value}"]) + "\n")
    env.chmod(0o600)


@mcp_app.command("init")
def init(
    write_env: Annotated[bool, typer.Option("--write-env", help="write INTEL_MCP_SECRET into .env")] = False,
    rotate: Annotated[
        bool, typer.Option(help="replace an existing secret: every token issued so far stops working")
    ] = False,
    host: Annotated[str, typer.Option(help="mac (this Mac's server) or vps (a separate secret for the VPS)")] = "mac",
) -> None:
    """Create a host's signing secret for HTTP tokens (one per host). Prints the key name only."""
    import secrets

    if not write_env:
        raise typer.BadParameter("pass --write-env (the secret is only ever written to .env, never printed)")
    key = "INTEL_MCP_SECRET" if host == "mac" else "INTEL_MCP_SECRET_VPS"
    if _secret_for(host) and not rotate:
        typer.echo(f"{key} already exists in .env (use --rotate to replace it)")
        return
    _write_env(key, secrets.token_urlsafe(48))
    typer.echo(f"{key} written to .env (value not shown)")
    if host == "vps":
        typer.echo(
            "Type it once into the VPS's deploy/.env with `read -rsp` (docs/runbooks/mcp.md); never paste it here"
        )


token_app = typer.Typer(no_args_is_help=True, help="HTTP tokens: issue into .env, mint for a headers helper, revoke")
mcp_app.add_typer(token_app, name="token")


def _secret_for(host: str) -> str | None:
    """Each host signs with its own secret (a Mac token never works on the VPS, nor the reverse)."""
    from dotenv import dotenv_values

    from intel.settings import McpSettings, repo_root

    if host not in ("mac", "vps"):
        raise typer.BadParameter("--host mac or vps")
    if host == "mac":
        return McpSettings().intel_mcp_secret
    value = dotenv_values(repo_root() / ".env").get("INTEL_MCP_SECRET_VPS")
    return str(value) if value else None


def _ttl(value: str) -> int:
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    try:
        return int(value[:-1]) * units[value[-1]] if value[-1] in units else int(value)
    except (ValueError, IndexError) as e:
        raise typer.BadParameter("ttl like 300, 30m, 12h or 30d") from e


@token_app.command("issue")
def token_issue(
    client: Annotated[str, typer.Option(help="who holds it, e.g. langchain or writer-agent")],
    profile: Profile,
    write_env: Annotated[
        str, typer.Option("--write-env", help="the .env key to store it in, e.g. INTEL_MCP_TOKEN_LANGCHAIN")
    ],
    ttl: Annotated[str, typer.Option(help="lifetime: 300, 30m, 12h, 30d (max 90d)")] = "30d",
    max_sensitivity: Annotated[
        str, typer.Option(help="S1 keeps review and creator text (S2) away from this client")
    ] = "S2",
    host: Annotated[str, typer.Option(help="mac or vps: which server the token is for")] = "mac",
) -> None:
    """Issue a token straight into .env (never printed). Prints its id, which is what you'd revoke."""
    from intel.mcp_server import auth
    from intel.mcp_server.gateway import profiles
    from intel.settings import required

    if profile not in profiles():
        raise typer.BadParameter(f"unknown profile {profile!r}")
    if not write_env.startswith("INTEL_MCP_TOKEN_"):
        raise typer.BadParameter("the key must start with INTEL_MCP_TOKEN_")
    key = "INTEL_MCP_SECRET" if host == "mac" else "INTEL_MCP_SECRET_VPS"
    token, jti = auth.issue(required(_secret_for(host), key), client, profile, _ttl(ttl), host, max_sensitivity)
    _write_env(write_env, token)
    typer.echo(f"{write_env} written to .env ({profile}, {ttl}, ceiling {max_sensitivity}); jti {jti}")


@token_app.command("header")
def token_header(profile: Profile = "owner", ttl: int = 300) -> None:
    """For Claude Code's headersHelper only: prints {"Authorization": "Bearer ..."} with a short-lived token.
    Refuses to print to a terminal and outside the helper (CLAUDE_CODE_MCP_SERVER_NAME), so a token never lands
    in a shell history or a transcript."""
    import os
    import sys

    from intel.mcp_server import auth
    from intel.settings import McpSettings, required

    if sys.stdout.isatty() or not os.environ.get("CLAUDE_CODE_MCP_SERVER_NAME"):
        typer.echo("refused: only Claude Code's headersHelper may call this", err=True)
        raise typer.Exit(2)
    if not 30 <= ttl <= 3600:
        raise typer.BadParameter("ttl must be 30 to 3600 seconds")
    s = McpSettings()
    token, _ = auth.issue(
        required(s.intel_mcp_secret, "INTEL_MCP_SECRET"), "claude-code", profile, ttl, s.intel_mcp_host
    )
    sys.stdout.write(json.dumps({"Authorization": f"Bearer {token}"}))


@token_app.command("revoke")
def token_revoke(jti: str) -> None:
    """Revoke one token by its id; servers notice within a minute (no restart)."""
    from intel.mcp_server.auth import Revocations
    from intel.settings import McpSettings

    if not jti.isalnum():
        raise typer.BadParameter("a jti is the hex id printed when the token was issued")
    Revocations(McpSettings().revoked_file).add(jti)
    typer.echo(f"{jti} revoked")


@mcp_app.command("loadtest")
def loadtest(
    clients: int = 10,
    rounds: int = 3,
    url: Annotated[str | None, typer.Option(help="default: the local listener's owner path")] = None,
) -> None:
    """N concurrent clients over HTTP against a running server; p50/p95 and errors per tool (Mac, by hand)."""
    from intel.mcp_server import auth
    from intel.mcp_server.loadtest import run
    from intel.settings import McpSettings, required

    s = McpSettings()
    secret = required(s.intel_mcp_secret, "INTEL_MCP_SECRET")
    tokens = [auth.issue(secret, f"loadtest-{i}", "owner", 3600, s.intel_mcp_host)[0] for i in range(clients)]
    target = url or f"http://127.0.0.1:{s.intel_mcp_http_port}/mcp/owner/"
    typer.echo(json.dumps(asyncio.run(run(target, tokens, clients, rounds)), indent=2))


@mcp_app.command("catalog")
def catalog() -> None:
    """Regenerate docs/mcp/CATALOG.md and the tool-contract snapshots from the code (tests fail when stale)."""
    from intel.mcp_server import catalog as cat
    from intel.mcp_server.deps import Deps
    from intel.mcp_server.kit.middleware import stdio_principal
    from intel.settings import McpSettings

    deps = Deps.from_settings(McpSettings(), stdio_principal())
    cat.write(deps)
    cat.write_snapshots(deps)
    typer.echo(f"wrote {cat.CATALOG} and {cat.SNAPSHOTS}/*.json (review the diff: tool contracts only grow)")


@mcp_app.command("config")
def config(
    client: Annotated[
        str, typer.Option(help="claude-code, claude-desktop, langchain or openai-agents")
    ] = "claude-code",
    profile: Profile = "owner",
    transport: Annotated[str, typer.Option(help="stdio or http")] = "stdio",
) -> None:
    """Print a ready client config. It names .env keys, never values."""
    from intel.mcp_server.clients import client_config

    try:
        typer.echo(client_config(client, profile, transport))
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


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
def audit(action: Annotated[str, typer.Argument(help="verify | tail | stats")] = "verify") -> None:
    """Check the audit hash chain, show its last entries, or per-tool stats (calls, errors, p50/p95, tokens)."""
    from intel.mcp_server.kit.audit import AuditLog
    from intel.settings import McpSettings

    log = AuditLog(McpSettings().audit_dir)
    if action == "verify":
        result = log.verify()
        typer.echo(json.dumps(result))
        raise typer.Exit(0 if result["ok"] else 1)
    if action == "stats":
        from intel.mcp_server.kit.audit import stats

        typer.echo(f"{'tool':<24}{'calls':>7}{'errors':>8}{'denied':>8}{'p50 ms':>9}{'p95 ms':>9}{'tokens':>8}")
        for name, r in stats(log.entries()).items():
            typer.echo(
                f"{name:<24}{r['calls']:>7}{r['errors']:>8}{r['denied']:>8}{r['p50_ms']:>9}{r['p95_ms']:>9}"
                f"{r['tokens_mean'] if r['tokens_mean'] is not None else '':>8}"
            )
        return
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
    from intel.rag import pack as kp
    from intel.rag.lifecycle import load_and_gate
    from intel.settings import McpSettings

    s = McpSettings()
    pid = pack_id or kp.active_id(s.pack_dir)
    if pid is None:
        raise typer.BadParameter("need a pack id (or an active pack)")
    try:
        out = load_and_gate(s, pid, skip_gate, activate, echo=typer.echo)
    except kp.PackError as e:
        raise typer.BadParameter(str(e)) from e
    if out["gate"] is not None:
        typer.echo(json.dumps(out["gate"], indent=2))
    if not activate:
        return
    if not out["active"]:
        typer.secho(f"gate FAILED: {pid} stays staged; the active pack is unchanged", fg="red")
        raise typer.Exit(1)
    typer.secho(f"{pid} is active{' (gate skipped)' if skip_gate else ' (gate passed)'}", fg="green")


def _upload(tgz: Any) -> None:
    """Door 2: the upload-only inbox, archive first and checksum last, then a checksum dry run (intel.package)."""
    from intel.package import upload
    from intel.settings import get_settings, required

    s = get_settings()
    upload(tgz, required(s.dk_inbox_ssh, "DK_INBOX_SSH"), required(s.dk_inbox_ssh_key, "DK_INBOX_SSH_KEY"))


@pack_app.command("ship")
def pack_ship(
    pack_id: Annotated[str | None, typer.Argument(help="default: the active pack")] = None,
    models: Annotated[bool, typer.Option("--models", help="also send the serving models (once; ~1.2 GB)")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="build the packages, don't upload")] = False,
) -> None:
    """Send a gated knowledge pack (and, once, the serving models) to the VPS through the upload-only inbox (door 2).
    intel-api verifies it and intel-mcp loads it within ~5 minutes (ADR 0030)."""
    from intel.rag import pack as kp
    from intel.rag import ship
    from intel.rag.embed import OnnxEmbedder
    from intel.rag.rerank import ONNX as RERANKERS
    from intel.settings import McpSettings

    s = McpSettings()
    pid = pack_id or kp.active_id(s.pack_dir)
    if pid is None:
        raise typer.BadParameter("no active pack")
    out = s.data_dir / "packages" / "knowledge"
    tgzs = []
    if models:
        repos = [
            OnnxEmbedder.ONNX[str(s.intel_rag_embedder).removeprefix("onnx:")][0],
            RERANKERS[str(s.intel_rag_reranker).removeprefix("onnx:")][0],
        ]
        tgzs.append(ship.models_package(s.model_dir, repos, out))
    try:
        tgzs.append(ship.pack_package(s.pack_dir, pid, out))
    except ship.ShipError as e:
        raise typer.BadParameter(str(e)) from e
    for tgz in tgzs:
        size = tgz.stat().st_size / 1e6
        if dry_run:
            typer.echo(f"built {tgz.name} ({size:.0f} MB)")
            continue
        _upload(tgz)
        typer.echo(f"uploaded {tgz.name} ({size:.0f} MB); intel-mcp loads it within ~5 minutes")


@pack_app.command("rollback")
def pack_rollback(
    pack_id: Annotated[str, typer.Argument(help="the earlier pack to make active again")],
    vps: Annotated[bool, typer.Option("--vps", help="on the VPS (a small marker through door 2)")] = False,
) -> None:
    """Make an earlier pack active again: here (files; the database follows on `pack load`), or on the VPS, where the
    pack's bytes are already present and a tiny marker through the inbox switches it."""
    from intel.rag import pack as kp
    from intel.rag import ship
    from intel.settings import McpSettings

    s = McpSettings()
    if not vps:
        kp.activate(s.pack_dir, pack_id)
        typer.echo(f"{pack_id} is active on this Mac; run `uv run intel pack load {pack_id}` for the database")
        return
    tgz = ship.activate_package(pack_id, s.data_dir / "packages" / "knowledge")
    _upload(tgz)
    typer.echo(f"rollback marker for {pack_id} uploaded; the VPS switches within ~5 minutes (Telegram confirms)")


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


if __name__ == "__main__":  # the intel-mcp container's entry point: only the MCP commands, no training imports
    mcp_app()
