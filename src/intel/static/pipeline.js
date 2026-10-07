// DK Intelligence pipeline view. Reads its configuration from data-* attributes on #pipeline (no inline script, so it
// works under a strict Content-Security-Policy):
//   data-def    URL of the pipeline definition (nodes, edges, lanes, steps)
//   data-state  URL of the live state, polled every 1.5 s
//   data-run    optional: URL to POST to start a run (Mac Control Room only), with header X-Intel-Token = data-token
(function () {
  "use strict";
  const root = document.getElementById("pipeline");
  if (!root) return;
  const cfg = root.dataset;
  const W = 168, Hh = 66, GX = 46, GY = 30, PAD = 22, TOP = 34;
  const ICON = { done: "✓", running: "⏳", failed: "✗", skipped: "–", pending: "·", service: "◆", waiting: "?", stalled: "⚠" };
  let def = null, state = null, selected = null;

  const el = (tag, attrs, parent) => {
    const ns = ["svg", "g", "rect", "text", "path", "defs", "marker", "title"].includes(tag) ? "http://www.w3.org/2000/svg" : null;
    const e = ns ? document.createElementNS(ns, tag) : document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "text") e.textContent = v; else e.setAttribute(k, v);
    }
    if (parent) parent.appendChild(e);
    return e;
  };
  const fmtTime = (iso) => iso ? new Date(iso).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "";
  const pos = (n) => ({ x: PAD + n.col * (W + GX), y: TOP + PAD + n.row * (Hh + GY) });

  root.innerHTML = "";
  const head = el("div", { class: "pl-head" }, root);
  const title = el("h2", { text: "Weekly training pipeline" }, head);
  const status = el("span", { class: "pl-status", text: "loading…" }, head);
  const sub = el("span", { class: "pl-muted" }, head);
  let runBtn = null;
  if (cfg.run) {
    runBtn = el("button", { class: "pl-run", type: "button", text: "▶ Run training now" }, head);
    runBtn.addEventListener("click", startRun);
  }
  const bar = el("div", { class: "pl-bar" }, root);
  const barFill = el("div", {}, bar);
  const graph = el("div", { class: "pl-graph" }, root);
  const cols = el("div", { class: "pl-cols" }, root);
  const detail = el("div", { class: "pl-card" }, cols);
  const side = el("div", { class: "pl-card" }, cols);

  function stepOf(n) {
    if (!state || !state.progress) return n.step ? "pending" : "service";
    if (!n.step) {
      if (n.id === "approve" && state.progress.result === "pass") return "waiting";
      return "service";
    }
    const st = state.progress.steps[n.step];
    if (st && st.status === "running" && state.stalled) return "stalled";
    return st ? st.status : "pending";
  }

  function draw() {
    graph.innerHTML = "";
    const maxCol = Math.max(...def.nodes.map((n) => n.col)), maxRow = Math.max(...def.nodes.map((n) => n.row));
    const width = PAD * 2 + (maxCol + 1) * W + maxCol * GX, height = TOP + PAD * 2 + (maxRow + 1) * Hh + maxRow * GY + 36;
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Pipeline diagram" }, graph);
    const defs = el("defs", {}, svg);
    for (const [id, cls] of [["arrow", "pend"], ["arrow-ok", "ok"], ["arrow-run", "run"], ["arrow-svc", "svc"]]) {
      const m = el("marker", { id, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, defs);
      el("path", { d: "M0,0 L10,5 L0,10 z", style: `fill: var(--${cls})` }, m);
    }
    // lanes
    const lanes = {};
    for (const n of def.nodes) (lanes[n.lane] = lanes[n.lane] || []).push(n.col);
    for (const [lane, cs] of Object.entries(lanes)) {
      const a = Math.min(...cs), b = Math.max(...cs);
      const g = el("g", { class: "lane" }, svg);
      const x = PAD + a * (W + GX) - GX / 2 + 4, w = (b - a + 1) * (W + GX) - 8;
      el("rect", { x, y: 6, width: w, height: height - 12, rx: 12 }, g);
      el("text", { x: x + 12, y: 26, text: def.lanes[lane] || lane }, g);
    }
    // edges
    const byId = Object.fromEntries(def.nodes.map((n) => [n.id, n]));
    for (const [a, b] of def.edges) {
      const na = byId[a], nb = byId[b];
      if (!na || !nb) continue;
      const pa = pos(na), pb = pos(nb);
      const sa = stepOf(na), sb = stepOf(nb);
      let d, cls = "edge", mk = "arrow";
      if (nb.col <= na.col) {          // feedback loop: route under the diagram
        const yb = height - 14;
        d = `M${pa.x + W / 2},${pa.y + Hh} L${pa.x + W / 2},${yb} L${pb.x + W / 2},${yb} L${pb.x + W / 2},${pb.y + Hh}`;
        cls += " loop"; mk = "arrow-svc";
      } else {
        const x1 = pa.x + W, y1 = pa.y + Hh / 2, x2 = pb.x, y2 = pb.y + Hh / 2, mx = (x1 + x2) / 2;
        d = `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2 - 2},${y2}`;
        if (sb === "running") { cls += " flow"; mk = "arrow-run"; }
        else if (["done", "service"].includes(sa) && ["done", "waiting"].includes(sb)) { cls += " done"; mk = "arrow-ok"; }
      }
      el("path", { d, class: cls, "marker-end": `url(#${mk})` }, svg);
    }
    // nodes
    for (const n of def.nodes) {
      const st = stepOf(n), p = pos(n);
      const g = el("g", { class: `node ${st}${selected === n.id ? " selected" : ""}`, tabindex: 0, role: "button",
        transform: `translate(${p.x},${p.y})` }, svg);
      el("title", { text: n.label }, g);
      el("rect", { width: W, height: Hh, rx: 10 }, g);
      el("text", { class: "i", x: 12, y: 24, text: ICON[st] || "" }, g);
      el("text", { class: "t", x: 32, y: 24, text: n.label }, g);
      const note = n.step && state && state.progress && state.progress.steps[n.step] ? state.progress.steps[n.step].note : null;
      const subtxt = (note || n.sub || "").slice(0, 30);
      el("text", { class: "s", x: 12, y: 46, text: subtxt }, g);
      const pick = () => { selected = n.id; draw(); showDetail(); };
      g.addEventListener("click", pick);
      g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") pick(); });
    }
  }

  function showDetail() {
    detail.innerHTML = "";
    const n = def.nodes.find((x) => x.id === selected);
    if (!n) { el("p", { class: "pl-muted", text: "Click a box to see what it does and what happened." }, detail); return; }
    const st = stepOf(n);
    el("h3", { text: `${ICON[st] || ""} ${n.label}` }, detail);
    el("p", { class: "pl-muted", text: n.desc || "" }, detail);
    const t = el("table", {}, detail);
    const row = (k, v) => { if (v === undefined || v === null || v === "") return; const tr = el("tr", {}, t); el("td", { text: k }, tr); el("td", { text: String(v) }, tr); };
    row("Status", st);
    const s = n.step && state && state.progress ? state.progress.steps[n.step] : null;
    if (s) {
      row("Started", fmtTime(s.started_at)); row("Finished", fmtTime(s.finished_at));
      if (s.started_at && s.finished_at) row("Took", `${Math.max(1, Math.round((new Date(s.finished_at) - new Date(s.started_at)) / 1000))} s`);
      row("Result", s.note);
      for (const [k, v] of Object.entries(s.details || {})) row(k, typeof v === "boolean" ? (v ? "yes" : "no") : v);
    }
  }

  function showSide() {
    side.innerHTML = "";
    el("h3", { text: "Runs" }, side);
    if (state.due_text) el("p", { text: `This week: ${state.due_text}` }, side);
    const ul = el("ul", { class: "pl-hist" }, side);
    for (const h of state.history || []) {
      const li = el("li", {}, ul);
      const cls = h.status === "error" ? "bad" : h.status === "pass" ? "ok" : "";
      el("b", { class: cls, text: h.status }, li);
      li.appendChild(document.createTextNode(` ${h.run_id}${h.failed_step ? " at " + h.failed_step : ""}${h.new != null ? ` · new ${h.new} vs ${h.current}` : ""}`));
    }
    if (!(state.history || []).length) el("li", { class: "pl-muted", text: "no runs yet" }, ul);
    if (state.log) {
      el("h3", { text: "Live log" }, side);
      const pre = el("pre", { class: "pl-log", text: state.log.join("\n") }, side);
      pre.scrollTop = pre.scrollHeight;
    }
  }

  function header() {
    const p = state.progress;
    status.className = "pl-status";
    if (state.running) { status.textContent = "⏳ running"; status.classList.add("running"); }
    else if (state.stalled) { status.textContent = "⚠️ stalled"; status.classList.add("bad"); }
    else if (p && p.state === "finished") {
      status.textContent = `finished: ${p.result}`;
      status.classList.add(p.result === "error" ? "bad" : "ok");
    } else status.textContent = "idle";
    sub.textContent = p ? `run ${p.run_id} · started ${fmtTime(p.started_at)} · updated ${fmtTime(p.updated_at)}` : "no run yet";
    if (p) {
      const steps = Object.values(p.steps), total = steps.filter((s) => s.status !== "skipped").length || 1;
      barFill.style.width = `${Math.round(100 * steps.filter((s) => s.status === "done").length / total)}%`;
    }
    if (runBtn) { runBtn.disabled = !!state.running; runBtn.textContent = state.running ? "Running…" : "▶ Run training now"; }
    if (!selected && p) selected = (def.nodes.find((n) => n.step === p.current_step) || def.nodes.find((n) => n.id === "judge")).id;
  }

  async function poll() {
    try {
      const r = await fetch(cfg.state, { cache: "no-store" });
      if (r.ok) { state = await r.json(); header(); draw(); showDetail(); showSide(); }
    } catch (e) { status.textContent = "offline"; }
    setTimeout(poll, state && state.running ? 1500 : 5000);
  }

  async function startRun() {
    runBtn.disabled = true; runBtn.textContent = "Starting…";
    const r = await fetch(cfg.run, { method: "POST", headers: { "X-Intel-Token": cfg.token } });
    if (!r.ok) { alert((await r.json()).detail || "could not start"); }
    selected = "mip";
    setTimeout(poll, 800);
  }

  fetch(cfg.def).then((r) => r.json()).then((d) => { def = d; poll(); });
})();
