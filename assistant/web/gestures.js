// Hand gestures from 21 hand landmarks (MediaPipe order), plus the controller
// that turns gestures into hologram motion. No camera or drawing code here, so
// it can be tested on its own.
(function (root) {
  const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const TIPS = [8, 12, 16, 20], PIPS = [6, 10, 14, 18];

  // One hand -> { pinch, fist, open, palm: {x, y}, point: {x, y}, size }
  function read(hand) {
    const wrist = hand[0];
    const size = dist(wrist, hand[9]) || 1e-6;                 // wrist to middle knuckle
    const curled = TIPS.filter((tip, i) => dist(hand[tip], wrist) < dist(hand[PIPS[i]], wrist)).length;
    const fist = curled === 4;
    const pinchGap = dist(hand[4], hand[8]) / size;
    const pinch = !fist && pinchGap < 0.32;
    const palm = { x: (wrist.x + hand[5].x + hand[17].x) / 3, y: (wrist.y + hand[5].y + hand[17].y) / 3 };
    const point = { x: (hand[4].x + hand[8].x) / 2, y: (hand[4].y + hand[8].y) / 2 };
    return { pinch, fist, open: !fist && !pinch && curled === 0, palm, point, size };
  }

  // Turns a stream of hands into rotation, position, scale and explode amount.
  //   one hand pinching, moved   -> rotate
  //   two hands pinching         -> scale (pull apart / push together)
  //   a fist, moved              -> move
  //   two open palms             -> spread apart to explode, bring together to assemble
  class Controller {
    constructor() { this.reset(); }
    reset() {
      this.yaw = 0.6; this.pitch = 0.32; this.x = 0; this.y = 0; this.scale = 1;
      this.explodeTarget = 0; this.explode = 0; this.mode = "none"; this.anchor = null;
    }
    update(hands, dt) {
      const g = hands.map(read);
      const pinching = g.filter((h) => h.pinch), fists = g.filter((h) => h.fist), open = g.filter((h) => h.open);
      const mode = pinching.length === 2 ? "scale" : pinching.length === 1 ? "rotate"
        : open.length === 2 ? "explode" : fists.length ? "move" : "none";
      if (mode !== this.mode) { this.mode = mode; this.anchor = null; }
      if (mode === "rotate") {
        const p = pinching[0].point;
        if (this.anchor) {
          this.yaw += (p.x - this.anchor.x) * 5;
          this.pitch = clamp(this.pitch + (p.y - this.anchor.y) * 3, -1.2, 1.2);
        }
        this.anchor = { ...p };
      } else if (mode === "move") {
        const p = fists[0].palm;
        if (this.anchor) {
          this.x = clamp(this.x + (p.x - this.anchor.x), -0.6, 0.6);
          this.y = clamp(this.y + (p.y - this.anchor.y), -0.6, 0.6);
        }
        this.anchor = { ...p };
      } else if (mode === "scale") {
        const d = dist(pinching[0].point, pinching[1].point);
        if (!this.anchor) this.anchor = { d, scale: this.scale };
        this.scale = clamp(this.anchor.scale * (d / (this.anchor.d || 1e-6)), 0.4, 3);
      } else if (mode === "explode") {
        this.explodeTarget = clamp((dist(open[0].palm, open[1].palm) - 0.25) / 0.35, 0, 1);
      }
      this.explode += (this.explodeTarget - this.explode) * Math.min(1, (dt || 0.016) * 6);
      return g;
    }
  }

  root.Gestures = { read, Controller };
  if (typeof module !== "undefined") module.exports = { read, Controller };
})(typeof window !== "undefined" ? window : globalThis);
