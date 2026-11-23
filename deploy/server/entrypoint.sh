#!/bin/sh
# Secret files -> the settings the code reads (ADR 0044). Values are never printed. An empty file stays unset.
set -eu
S="${INTEL_SECRETS_DIR:-/run/secrets}"
read_secret() { [ -s "$S/$1" ] && tr -d '\r\n' < "$S/$1"; }
dsn() {  # user password-file host db -> postgresql DSN with the password URL-encoded
  pw=$(read_secret "$2") || return 0
  [ -n "${3:-}" ] || return 0
  enc=$(printf '%s' "$pw" | python -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.stdin.read(), safe=""))')
  printf 'postgresql://%s:%s@%s:5432/%s' "$1" "$enc" "$3" "$4"
}
if [ -z "${MIP_READER_DSN:-}" ]; then
  MIP_READER_DSN=$(dsn mip_reader mip_reader_password "${MIP_PG_HOST:-}" mip); export MIP_READER_DSN
fi
if [ -z "${INTEL_RAG_ADMIN_DSN:-}" ]; then
  INTEL_RAG_ADMIN_DSN=$(dsn rag_builder rag_builder_password "${RAG_PG_HOST:-}" rag); export INTEL_RAG_ADMIN_DSN
fi
if [ -z "${DK_WAREHOUSE_DSN:-}" ] && v=$(read_secret dk_warehouse_dsn); then DK_WAREHOUSE_DSN=$v; export DK_WAREHOUSE_DSN; fi
[ -s "$S/dk_warehouse_ssh_key" ] && export DK_WAREHOUSE_SSH_KEY="$S/dk_warehouse_ssh_key"
[ -s "$S/dk_inbox_ssh_key" ] && export DK_INBOX_SSH_KEY="$S/dk_inbox_ssh_key"
exec "$@"
