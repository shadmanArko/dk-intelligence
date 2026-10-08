# shellcheck shell=bash
# Shared helper (sourced): publish job metrics through node-exporter's textfile collector (ADR 0016).
# write_metrics <name> <<< "metric lines"  -> $INTEL_METRICS_DIR/<name>.prom, replaced atomically (never half-read).
write_metrics() {
  local dir="${INTEL_METRICS_DIR:-/opt/dhaka-kacchi/metrics}"
  [[ -d "$dir" ]] || return 0                       # monitoring not set up yet: nothing to do
  local tmp="$dir/.$1.prom.$$"
  cat > "$tmp" && chmod 644 "$tmp" && mv -f "$tmp" "$dir/$1.prom"
}
