// Hologram mode: your camera as the background, a 3D building model floating
// in front of you, and your hands controlling it (see gestures.js).
// Also works without a camera: drag to rotate, wheel to scale, E to explode.
(() => {
  const $ = (id) => document.getElementById(id);
  const ASSETS = Object.assign({
    lib: "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs",
    wasm: "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm",
    model: "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
  }, window.HOLO_ASSETS || {});
  const HINTS = { none: "PINCH TO ROTATE  ·  FIST TO MOVE  ·  TWO PINCHES TO SCALE  ·  TWO OPEN PALMS: SPREAD TO EXPLODE",
    rotate: "ROTATING", move: "MOVING", scale: "SCALING", explode: "SPREAD TO EXPLODE  ·  CLOSE TO ASSEMBLE" };
  const root = $("holo"), video = $("holo-video"), canvas = $("holo-canvas"), ctx = canvas.getContext("2d");
  const control = new Gestures.Controller();
  let active = false, mirror = true, stream = null, landmarker = null, loading = null;
  let hands = [], fed = null, lastVideoTime = -1, last = 0, w = 0, h = 0;

  // ------------------------------------------------------------------ model
  // A six-storey building: slabs, columns and a core, grouped by level so the
  // exploded view lifts the levels apart.
  const LEVELS = 6, W = 1.5, D = 0.95, STOREY = 0.34;
  function box(cx, cy, cz, sx, sy, sz) {
    const c = [];
    for (const dx of [-1, 1]) for (const dy of [-1, 1]) for (const dz of [-1, 1])
      c.push([cx + dx * sx / 2, cy + dy * sy / 2, cz + dz * sz / 2]);
    const e = [[0, 1], [2, 3], [4, 5], [6, 7], [0, 2], [1, 3], [4, 6], [5, 7], [0, 4], [1, 5], [2, 6], [3, 7]];
    return e.map(([a, b]) => [c[a], c[b]]);
  }
  const levels = [];
  for (let i = 0; i < LEVELS; i++) {
    const lines = [...box(0, 0, 0, W, 0.035, D)];
    if (i < LEVELS - 1) {
      for (const x of [-W / 2 + 0.06, -W / 6, W / 6, W / 2 - 0.06]) for (const z of [-D / 2 + 0.06, D / 2 - 0.06])
        lines.push([[x, 0, z], [x, STOREY, z]]);
      lines.push(...box(0.18, STOREY / 2, 0, 0.36, STOREY, 0.3));        // lift and stair core
    }
    levels.push({ lines, label: i === LEVELS - 1 ? "ROOF" : `LEVEL ${String(i + 1).padStart(2, "0")}` });
  }

  function project(p, lift) {
    const s = control.scale;
    let [x, y, z] = [p[0], p[1] + lift, p[2]];
    const cy = Math.cos(control.yaw), sy = Math.sin(control.yaw);
    [x, z] = [x * cy + z * sy, -x * sy + z * cy];
    const cp = Math.cos(control.pitch), sp = Math.sin(control.pitch);
    [y, z] = [y * cp - z * sp, y * sp + z * cp];
    const k = 3.2 / (3.2 + z * s);                                       // perspective
    const unit = Math.min(w, h) * 0.22 * s;
    return [w * (0.5 + control.x) + x * unit * k, h * (0.42 + control.y) - y * unit * k, k];
  }

  function drawModel() {
    const gap = STOREY * (1 + control.explode * 0.9);
    const base = -(LEVELS - 1) * gap / 2;
    ctx.lineCap = "round";
    levels.forEach((level, i) => {
      const lift = base + i * gap;
      ctx.beginPath();
      for (const [a, b] of level.lines) {
        const [ax, ay] = project(a, lift), [bx, by] = project(b, lift);
        ctx.moveTo(ax, ay); ctx.lineTo(bx, by);
      }
      ctx.strokeStyle = "rgba(79, 227, 255, .9)"; ctx.lineWidth = 1.4;
      ctx.shadowColor = "#4fe3ff"; ctx.shadowBlur = 10; ctx.stroke(); ctx.shadowBlur = 0;
      if (control.explode > 0.35) {                                      // level tags in the exploded view
        const [lx, ly] = project([W / 2, 0, D / 2], lift);
        ctx.globalAlpha = Math.min(1, (control.explode - 0.35) * 3);
        ctx.strokeStyle = "rgba(79, 227, 255, .6)"; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(lx, ly); ctx.lineTo(lx + 34, ly - 10); ctx.lineTo(lx + 44, ly - 10); ctx.stroke();
        ctx.fillStyle = "#d9f8ff"; ctx.font = "600 12px Consolas, monospace"; ctx.fillText(level.label, lx + 50, ly - 6);
        ctx.globalAlpha = 1;
      }
    });
  }

  // ------------------------------------------------------------------ hands

  const toScreen = (p) => [(mirror ? 1 - p.x : p.x) * w, p.y * h];
  function drawHands(read, t) {
    read.forEach((g) => {
      const [px, py] = toScreen(g.palm), r = Math.max(26, g.size * h * 0.9);
      const on = g.pinch || g.fist;
      ctx.strokeStyle = on ? "rgba(255, 179, 71, .95)" : "rgba(79, 227, 255, .9)";
      ctx.shadowColor = ctx.strokeStyle; ctx.shadowBlur = 12; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(px, py, r, 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([r * 0.5, r * 0.35]); ctx.lineDashOffset = -t * 60;
      ctx.beginPath(); ctx.arc(px, py, r * 1.3, 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([]);
      if (g.pinch) {
        const [x, y] = toScreen(g.point);
        ctx.fillStyle = "rgba(255, 179, 71, .9)"; ctx.beginPath(); ctx.arc(x, y, 7, 0, Math.PI * 2); ctx.fill();
      }
      ctx.shadowBlur = 0;
    });
  }

  // ------------------------------------------------------------------- loop

  function resize() {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    w = root.clientWidth; h = root.clientHeight;
    canvas.width = w * dpr; canvas.height = h * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function frame(now) {
    if (!active) return;
    const dt = Math.min((now - last) / 1000, 0.05); last = now;
    if (fed) hands = fed;
    else if (landmarker && video.readyState >= 2 && video.currentTime !== lastVideoTime) {
      lastVideoTime = video.currentTime;
      try { hands = landmarker.detectForVideo(video, now).landmarks || []; } catch { hands = []; }
    }
    // Gestures are read in screen space, so a mirrored view moves the way the hand moves.
    const seen = hands.map((hd) => hd.map((p) => ({ x: mirror ? 1 - p.x : p.x, y: p.y, z: p.z })));
    const read = control.update(seen, dt);
    ctx.clearRect(0, 0, w, h);
    drawModel();
    drawHands(read.map((g, i) => Gestures.read(hands[i])), now / 1000);
    $("holo-hint").textContent = HINTS[control.mode];
    requestAnimationFrame(frame);
  }

  // ----------------------------------------------------------------- camera

  function status(text) { $("holo-status").textContent = text; }
  async function startCamera(deviceId) {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      status("NO CAMERA ACCESS HERE. DRAG TO ROTATE, SCROLL TO SCALE, PRESS E TO EXPLODE."); return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: deviceId ? { deviceId: { exact: deviceId } } : { facingMode: "user", width: { ideal: 1280 } } });
    } catch {
      status("CAMERA BLOCKED OR NOT FOUND. DRAG TO ROTATE, SCROLL TO SCALE, PRESS E TO EXPLODE."); return;
    }
    video.srcObject = stream; await video.play().catch(() => {});
    const cams = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
    const current = stream.getVideoTracks()[0].getSettings().deviceId;
    $("holo-camera").replaceChildren(...cams.map((c, i) =>
      Object.assign(document.createElement("option"), { value: c.deviceId, textContent: c.label || `Camera ${i + 1}`,
        selected: c.deviceId === current })));
    loadTracker();
  }
  function loadTracker() {
    if (landmarker || loading) return;
    status("LOADING HAND TRACKING ...");
    loading = (async () => {
      try {
        const vision = await import(ASSETS.lib);
        const files = await vision.FilesetResolver.forVisionTasks(ASSETS.wasm);
        landmarker = await vision.HandLandmarker.createFromOptions(files, {
          baseOptions: { modelAssetPath: ASSETS.model, delegate: "GPU" }, runningMode: "VIDEO", numHands: 2 });
        status("");
      } catch (e) {
        loading = null;
        status("HAND TRACKING COULD NOT LOAD (IT NEEDS INTERNET THE FIRST TIME). DRAG, SCROLL AND E STILL WORK.");
      }
    })();
  }

  // --------------------------------------------------------------- controls

  function open() {
    if (active) return;
    active = true; root.hidden = false; document.body.classList.add("holo-on");
    resize(); last = performance.now(); requestAnimationFrame(frame); startCamera();
  }
  function close() {
    active = false; root.hidden = true; document.body.classList.remove("holo-on");
    if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
  }
  function act(action) {
    if (action === "open") return open();
    if (action === "close") return close();
    if (action === "explode") control.explodeTarget = 1;
    if (action === "assemble") control.explodeTarget = 0;
    if (action === "reset") control.reset();
  }
  function setMirror(on) {
    mirror = on; video.style.transform = on ? "scaleX(-1)" : "none";
    $("holo-mirror").textContent = `MIRROR ${on ? "ON" : "OFF"}`;
  }
  $("holo-mirror").onclick = () => setMirror(!mirror);
  $("holo-bw").onclick = () => {
    const on = root.classList.toggle("bw"); $("holo-bw").textContent = `B&W ${on ? "ON" : "OFF"}`;
  };
  $("holo-dim").oninput = (e) => { video.style.opacity = 1 - e.target.value / 100; $("holo-dim-value").textContent = `${e.target.value}%`; };
  $("holo-camera").onchange = (e) => startCamera(e.target.value);
  $("holo-reset").onclick = () => control.reset();
  $("holo-close").onclick = close;
  addEventListener("resize", () => active && resize());
  addEventListener("keydown", (e) => {
    if (!active || document.activeElement.tagName === "INPUT") return;
    if (e.key === "e" || e.key === "E") control.explodeTarget = control.explodeTarget > 0.5 ? 0 : 1;
    if (e.key === "r" || e.key === "R") control.reset();
    if (e.key === "Escape") close();
  });
  let drag = null;
  canvas.addEventListener("pointerdown", (e) => { drag = [e.clientX, e.clientY]; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener("pointermove", (e) => {
    if (!drag) return;
    control.yaw += (e.clientX - drag[0]) * 0.008;
    control.pitch = Math.min(1.2, Math.max(-1.2, control.pitch + (e.clientY - drag[1]) * 0.006));
    drag = [e.clientX, e.clientY];
  });
  canvas.addEventListener("pointerup", () => { drag = null; });
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault(); control.scale = Math.min(3, Math.max(0.4, control.scale * (e.deltaY < 0 ? 1.08 : 0.92)));
  }, { passive: false });

  setMirror(true);
  // feed(): lets tests (or another tracker) supply hand landmarks directly.
  window.Holo = { open, close, act, control, feed: (h) => { fed = h; }, get active() { return active; } };
})();
