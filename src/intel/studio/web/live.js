// The living pipeline: a slow, calm picture of the studio at work. One glowing particle per idea drifts through the
// stations (Plan → Director → Write → Design → Render → Upload → Drafts); stations breathe while something happens there;
// finished drafts settle into a pool that fills like water. Idle, it keeps breathing. Soft sound is optional and off.
(function () {
  const HUES = {"one-take": 28, "kinetic-text": 45, "photo-motion": 330, "beat-reel": 12, "collage": 160, "split": 200,
    "steps": 95, "statement": 275, "photo": 52, "card": 20, "price": 40, "compare": 180, "?": 38};
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const lerp = (a, b, t) => a + (b - a) * t;
  const hsl = (h, s, l, a) => `hsla(${h},${s}%,${l}%,${a})`;

  window.startLive = function (canvas, opts) {
    const ctx = canvas.getContext("2d");
    let scene = null, W = 0, H = 0, dpr = 1, last = 0, t0 = performance.now(), hidden = false, tick = 0;
    const P = new Map();          // id → smoothed particle
    const dust = Array.from({length: 70}, () => ({x: Math.random(), y: Math.random(), r: 0.6 + Math.random() * 1.6, s: 0.02 + Math.random() * 0.05, p: Math.random() * 6.28}));
    let audio = null, soundOn = false, lastDrafts = 0;

    function resize() {
      dpr = Math.min(2, window.devicePixelRatio || 1);
      W = canvas.clientWidth; H = canvas.clientHeight;
      canvas.width = W * dpr; canvas.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    new ResizeObserver(resize).observe(canvas); resize();
    document.addEventListener("visibilitychange", () => { hidden = document.hidden; });

    async function poll() {
      try {
        const url = opts.demo ? `/api/live/demo?tick=${tick++}` : "/api/live";
        scene = await (await fetch(url)).json();
        if (soundOn && scene.drafts > lastDrafts) chime();
        lastDrafts = scene.drafts || 0;
      } catch (e) { /* the picture just keeps breathing */ }
      setTimeout(poll, opts.demo ? 900 : 2500);
    }
    poll();

    // ---- soft sound (synthesised here; nothing is downloaded; starts only after a click) ----
    function bed() {
      audio = new (window.AudioContext || window.webkitAudioContext)();
      const g = audio.createGain(); g.gain.value = 0.0; g.connect(audio.destination);
      [110, 165, 220.5].forEach((f, i) => { const o = audio.createOscillator(); o.type = "sine"; o.frequency.value = f; const og = audio.createGain(); og.gain.value = [0.5, 0.3, 0.15][i];
        const lfo = audio.createOscillator(); lfo.frequency.value = 0.05 + i * 0.03; const lg = audio.createGain(); lg.gain.value = 0.25; lfo.connect(lg); lg.connect(og.gain); o.connect(og); og.connect(g); o.start(); lfo.start(); });
      g.gain.linearRampToValueAtTime(0.035, audio.currentTime + 4);
    }
    function chime() {
      if (!audio) return; const now = audio.currentTime;
      [660, 880].forEach((f, i) => { const o = audio.createOscillator(); o.type = "sine"; o.frequency.value = f; const g = audio.createGain();
        g.gain.setValueAtTime(0, now + i * 0.12); g.gain.linearRampToValueAtTime(0.05, now + i * 0.12 + 0.03); g.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.12 + 2.2);
        o.connect(g); g.connect(audio.destination); o.start(now + i * 0.12); o.stop(now + i * 0.12 + 2.3); });
    }
    window.toggleLiveSound = function () { soundOn = !soundOn; if (soundOn && !audio) bed(); if (audio) audio.resume(); if (!soundOn && audio) audio.suspend(); return soundOn; };

    // ---- drawing ----
    const stationX = (i, n) => lerp(W * 0.09, W * 0.80, i / (n - 1));
    const riverY = (x, t) => H * 0.46 + Math.sin(x / W * 5.2 + t * 0.00035) * H * 0.055;

    function frame(now) {
      requestAnimationFrame(frame);
      if (hidden || now - last < 33) return; last = now;
      const t = reduced ? 0 : now - t0;
      const g = ctx.createLinearGradient(0, 0, 0, H); g.addColorStop(0, "#1a0a0b"); g.addColorStop(0.6, "#2b1013"); g.addColorStop(1, "#4a1419");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      ctx.globalCompositeOperation = "lighter";
      for (const d of dust) { const x = ((d.x + t * 0.000006 * d.s * 40) % 1) * W, y = (d.y * H + Math.sin(t * 0.0003 + d.p) * 12);
        ctx.fillStyle = hsl(38, 70, 70, 0.10 + 0.08 * Math.sin(t * 0.0006 + d.p)); ctx.beginPath(); ctx.arc(x, y, d.r, 0, 6.28); ctx.fill(); }
      const stations = (scene && scene.stations) || ["plan", "director", "write", "design", "render", "upload", "drafts"].map((s) => ({id: s, label: s, count: 0, active: false}));
      const n = stations.length, xs = stations.map((_, i) => stationX(i, n));
      // the river: a soft band of light
      for (let w = 3; w >= 1; w--) { ctx.strokeStyle = hsl(30, 80, 55, 0.05 * w); ctx.lineWidth = 16 * w; ctx.lineCap = "round"; ctx.beginPath();
        for (let x = W * 0.04; x <= W * 0.86; x += 8) { const y = riverY(x, t); x === W * 0.04 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); } ctx.stroke(); }
      ctx.globalCompositeOperation = "source-over";
      // stations
      stations.forEach((s, i) => { const x = xs[i], y = riverY(x, t), br = s.active ? 1 + 0.12 * Math.sin(t * 0.0021 + i) : 1 + 0.03 * Math.sin(t * 0.001 + i);
        const rg = ctx.createRadialGradient(x, y, 2, x, y, 46 * br); rg.addColorStop(0, hsl(38, 90, 62, s.active ? 0.42 : 0.18)); rg.addColorStop(1, hsl(38, 90, 62, 0));
        ctx.fillStyle = rg; ctx.beginPath(); ctx.arc(x, y, 46 * br, 0, 6.28); ctx.fill();
        ctx.strokeStyle = hsl(38, 80, 70, s.active ? 0.9 : 0.4); ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, 14 * br, 0, 6.28); ctx.stroke();
        ctx.fillStyle = hsl(40, 40, 85, 0.85); ctx.font = `600 ${W < 700 ? 11 : 13}px system-ui`; ctx.textAlign = "center";
        ctx.fillText(s.label, x, y + (W < 700 && i % 2 ? 62 : 44));  // on a narrow screen the labels take turns, so none overlaps
        if (s.count) { ctx.fillStyle = hsl(38, 90, 70, 0.95); ctx.font = "700 12px system-ui"; ctx.fillText(String(s.count), x, y - 26); } });
      // the pool of finished drafts
      const px = W * 0.88, pw = W * 0.09, ph = H * 0.5, py = H * 0.2, total = Math.max(1, (scene && scene.particles ? scene.particles.length : 1));
      const level = scene ? Math.min(1, (scene.drafts || 0) / total) : 0;
      ctx.save(); ctx.beginPath(); ctx.roundRect(px, py, pw, ph, 18); ctx.clip();
      ctx.fillStyle = "rgba(255,244,222,0.05)"; ctx.fillRect(px, py, pw, ph);
      const top = py + ph * (1 - level); const wg = ctx.createLinearGradient(0, top, 0, py + ph); wg.addColorStop(0, "rgba(244,176,46,0.65)"); wg.addColorStop(1, "rgba(184,100,42,0.55)");
      ctx.fillStyle = wg; ctx.beginPath(); ctx.moveTo(px, py + ph); for (let x = 0; x <= pw; x += 4) ctx.lineTo(px + x, top + Math.sin(x / 9 + t * 0.002) * 3); ctx.lineTo(px + pw, py + ph); ctx.fill(); ctx.restore();
      ctx.strokeStyle = hsl(38, 70, 70, 0.4); ctx.lineWidth = 1.5; ctx.beginPath(); ctx.roundRect(px, py, pw, ph, 18); ctx.stroke();
      // ideas
      if (scene && scene.particles) {
        const seen = new Set(); const slot = {};
        const counts = {}; scene.particles.forEach((p) => { counts[p.station] = (counts[p.station] || 0) + 1; });
        scene.particles.forEach((p) => { seen.add(p.id); const si = stations.findIndex((s) => s.id === p.station);
          const k = (slot[p.station] = (slot[p.station] || 0) + 1);
          let tx, ty;
          if (p.station === "held") { tx = W * 0.18 + (k * 41) % (W * 0.5); ty = H * 0.84 + Math.sin(t * 0.0008 + k) * 6; }
          else if (p.station === "drafts") { tx = px + pw * 0.5 + Math.sin(k * 2.1) * pw * 0.3; ty = py + ph * (1 - level) + 10 + (k % 5) * 6; }
          else { const crowd = Math.sqrt(counts[p.station] || 1), a = t * 0.0006 + k * 1.3, rr = 20 + (k % 6) * 8 + crowd * 4; tx = xs[si] + Math.cos(a) * rr; ty = riverY(xs[si], t) + Math.sin(a) * rr * 0.7; }
          let q = P.get(p.id); if (!q) { q = {x: xs[0] - 30, y: riverY(xs[0], t), trail: []}; P.set(p.id, q); }
          q.x = lerp(q.x, tx, 0.035); q.y = lerp(q.y, ty, 0.035); q.trail.push([q.x, q.y]); if (q.trail.length > 14) q.trail.shift();
          const hue = HUES[p.template] ?? 38, amber = p.held;
          ctx.globalCompositeOperation = "lighter";
          q.trail.forEach(([x, y], i) => { ctx.fillStyle = amber ? hsl(35, 60, 50, 0.02 * i) : hsl(hue, 90, 62, 0.025 * i); ctx.beginPath(); ctx.arc(x, y, 2 + i * 0.25, 0, 6.28); ctx.fill(); });
          const gl = ctx.createRadialGradient(q.x, q.y, 0, q.x, q.y, 16); const soft = 1 / (1 + (counts[p.station] || 1) * 0.07);  // a crowded station glows softer, so the light stays gentle
          gl.addColorStop(0, amber ? hsl(35, 60, 60, 0.45 * soft) : hsl(hue, 95, 68, 0.9 * soft)); gl.addColorStop(1, hsl(hue, 95, 60, 0));
          ctx.fillStyle = gl; ctx.beginPath(); ctx.arc(q.x, q.y, 16, 0, 6.28); ctx.fill(); ctx.globalCompositeOperation = "source-over"; });
        for (const id of [...P.keys()]) if (!seen.has(id)) P.delete(id);
      }
      // quiet numbers
      ctx.textAlign = "left"; ctx.fillStyle = hsl(40, 30, 80, 0.8); ctx.font = "500 12px system-ui";
      if (scene && scene.running) {
        ctx.fillText(`${scene.phase || "working"} · ${scene.drafts} of ${scene.particles.length} made · ${Math.round(scene.seconds / 60)} min`, 22, 28);
        if (scene.cap) { const share = Math.min(1, scene.tokens / scene.cap); ctx.strokeStyle = hsl(38, 70, 70, 0.3); ctx.lineWidth = 3; ctx.beginPath(); ctx.arc(30, 58, 11, 0, 6.28); ctx.stroke();
          ctx.strokeStyle = hsl(38, 90, 62, 0.9); ctx.beginPath(); ctx.arc(30, 58, 11, -1.57, -1.57 + share * 6.28); ctx.stroke(); ctx.fillText(`${(scene.tokens / 1000).toFixed(0)}k of ${(scene.cap / 1e6).toFixed(0)}M tokens`, 50, 62); }
      } else { ctx.fillText(scene ? "Resting. Nothing is running." : "Waking up…", 22, 28);
        const mx = W * 0.5, my = H * 0.16 + Math.sin(t * 0.0004) * 4; ctx.fillStyle = hsl(45, 70, 85, 0.5); ctx.beginPath(); ctx.arc(mx, my, 9, 0, 6.28); ctx.fill(); ctx.fillStyle = "#2b1013"; ctx.beginPath(); ctx.arc(mx + 4, my - 2, 8, 0, 6.28); ctx.fill(); }
    }
    requestAnimationFrame(frame);
  };
})();
