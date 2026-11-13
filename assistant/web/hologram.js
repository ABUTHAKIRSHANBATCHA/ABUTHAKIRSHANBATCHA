// Holographic core: a rotating point-cloud sphere with orbit rings, drawn on a
// 2D canvas so it runs smoothly on phones. Its motion follows body[data-state].
(() => {
  const canvas = document.getElementById("stage");
  const ctx = canvas.getContext("2d");
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const POINTS = 620;
  const pts = [];
  for (let i = 0; i < POINTS; i++) {           // Fibonacci sphere
    const y = 1 - (i / (POINTS - 1)) * 2;
    const r = Math.sqrt(1 - y * y);
    const a = i * Math.PI * (3 - Math.sqrt(5));
    pts.push([Math.cos(a) * r, y, Math.sin(a) * r, Math.random() * Math.PI * 2]);
  }
  const MODES = {
    idle:      { spin: 0.12, pulse: 0.02, speed: 1.0, jitter: 0.000, hue: 190 },
    listening: { spin: 0.30, pulse: 0.07, speed: 5.5, jitter: 0.012, hue: 186 },
    thinking:  { spin: 1.50, pulse: 0.03, speed: 3.0, jitter: 0.030, hue: 40 },
    speaking:  { spin: 0.38, pulse: 0.11, speed: 9.0, jitter: 0.008, hue: 190 },
  };
  const cur = { ...MODES.idle };
  let w = 0, h = 0, dpr = 1, angle = 0, last = performance.now();

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = canvas.clientWidth; h = canvas.clientHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  addEventListener("resize", resize);
  resize();

  function ring(cx, cy, rx, ry, rot, dash, alpha, width, color) {
    ctx.save();
    ctx.translate(cx, cy);
    ctx.beginPath();
    ctx.setLineDash(dash);
    ctx.lineDashOffset = -rot * rx;
    ctx.ellipse(0, 0, rx, ry, 0, 0, Math.PI * 2);
    ctx.strokeStyle = color.replace("A", alpha);
    ctx.lineWidth = width;
    ctx.stroke();
    ctx.restore();
  }

  function frame(now) {
    const dt = Math.min((now - last) / 1000, 0.05); last = now;
    const target = MODES[document.body.dataset.state] || MODES.idle;
    for (const k in cur) cur[k] += (target[k] - cur[k]) * Math.min(1, dt * 4);
    angle += cur.spin * dt;
    const t = now / 1000;

    ctx.clearRect(0, 0, w, h);
    const narrow = w < 820;
    const cx = w / 2, cy = h * (narrow ? 0.40 : 0.38);
    const base = Math.min(w * (narrow ? 0.34 : 0.2), h * 0.24);
    // Speaking uses an irregular pulse so it reads as a voice, not a metronome.
    const voice = Math.sin(t * cur.speed) * 0.6 + Math.sin(t * cur.speed * 1.7 + 1) * 0.4;
    const R = base * (1 + cur.pulse * voice);
    const color = `hsla(${cur.hue.toFixed(0)}, 100%, 66%, A)`;

    // Projection cone and floor rings, as if the core is projected from below.
    const floorY = cy + base * 1.55;
    const cone = ctx.createLinearGradient(0, floorY, 0, cy);
    cone.addColorStop(0, color.replace("A", 0.16));
    cone.addColorStop(1, color.replace("A", 0));
    ctx.fillStyle = cone;
    ctx.beginPath();
    ctx.moveTo(cx - base * 0.22, floorY); ctx.lineTo(cx - base * 1.05, cy);
    ctx.lineTo(cx + base * 1.05, cy); ctx.lineTo(cx + base * 0.22, floorY);
    ctx.fill();
    for (let i = 0; i < 3; i++) {
      const k = 0.5 + i * 0.38;
      ring(cx, floorY, base * k, base * k * 0.16, angle * (i % 2 ? -0.6 : 0.8), [base * 0.2, base * 0.07],
           0.5 - i * 0.14, 1.2, color);
    }

    // Core glow.
    const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.5);
    glow.addColorStop(0, color.replace("A", 0.22));
    glow.addColorStop(0.5, color.replace("A", 0.06));
    glow.addColorStop(1, color.replace("A", 0));
    ctx.fillStyle = glow;
    ctx.fillRect(cx - R * 1.6, cy - R * 1.6, R * 3.2, R * 3.2);

    // Point cloud.
    const sinA = Math.sin(angle), cosA = Math.cos(angle);
    const tilt = 0.38, sinT = Math.sin(tilt), cosT = Math.cos(tilt);
    for (const p of pts) {
      const wob = 1 + cur.jitter * 8 * Math.sin(t * 6 + p[3]);
      const x = (p[0] * cosA + p[2] * sinA) * wob;
      const z0 = (-p[0] * sinA + p[2] * cosA) * wob;
      const y = p[1] * wob * cosT - z0 * sinT;
      const z = p[1] * wob * sinT + z0 * cosT;
      const depth = (z + 1) / 2;                 // 0 back .. 1 front
      const scale = 0.82 + depth * 0.3;
      ctx.fillStyle = color.replace("A", (0.12 + depth * 0.8).toFixed(2));
      const s = 0.7 + depth * 1.5;
      ctx.fillRect(cx + x * R * scale - s / 2, cy + y * R * scale - s / 2, s, s);
    }

    // Orbit rings and tick marks around the core.
    ring(cx, cy, R * 1.28, R * 1.28, angle * 0.7, [R * 0.9, R * 0.22], 0.55, 1.4, color);
    ring(cx, cy, R * 1.42, R * 1.42, -angle * 0.45, [2, R * 0.075], 0.5, 5, color);
    ring(cx, cy, R * 1.6, R * 1.6, angle * 0.25, [R * 0.35, R * 1.3], 0.35, 1, color);
    ring(cx, cy, R * 1.72, R * 0.42, angle * 1.1, [R * 0.6, R * 0.5], 0.3, 1, color);

    if (!still) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
  if (still) new MutationObserver(() => requestAnimationFrame(frame))
    .observe(document.body, { attributes: true, attributeFilter: ["data-state"] });
})();
