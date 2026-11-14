// Synthetic hands for tests. pose: "open" | "fist" | "pinch"; (cx, cy) palm position; s size.
function hand(pose, cx, cy, s = 0.1) {
  const pts = Array.from({ length: 21 }, () => ({ x: cx, y: cy, z: 0 }));
  pts[0] = { x: cx, y: cy + s, z: 0 };                                   // wrist
  const fingers = [[5, 6, 7, 8, -0.45], [9, 10, 11, 12, -0.15], [13, 14, 15, 16, 0.15], [17, 18, 19, 20, 0.45]];
  for (const [mcp, pip, dip, tip, off] of fingers) {
    const x = cx + off * s;
    pts[mcp] = { x, y: cy, z: 0 };
    pts[pip] = { x, y: cy - 0.45 * s, z: 0 };
    const curled = pose === "fist";
    pts[dip] = { x, y: cy - (curled ? 0.2 : 0.75) * s, z: 0 };
    pts[tip] = { x, y: cy - (curled ? -0.1 : 1.0) * s, z: 0 };
  }
  pts[1] = { x: cx - 0.5 * s, y: cy + 0.7 * s, z: 0 }; pts[2] = { x: cx - 0.75 * s, y: cy + 0.4 * s, z: 0 };
  pts[3] = { x: cx - 0.9 * s, y: cy + 0.1 * s, z: 0 }; pts[4] = { x: cx - 1.0 * s, y: cy - 0.2 * s, z: 0 };
  if (pose === "pinch") {                                                // thumb tip meets index tip
    pts[8] = { x: cx - 0.5 * s, y: cy - 0.75 * s, z: 0 }; pts[4] = { x: cx - 0.56 * s, y: cy - 0.68 * s, z: 0 };
  }
  return pts;
}

// Run with:  node tests/web.test.js
const assert = require("assert");
const { read, Controller } = require("../assistant/web/gestures.js");
const { strip } = require("../assistant/web/wake.js");

// Wake phrase
assert.equal(strip("Hey Lumi, open Revit", "Lumi"), "open Revit");
assert.equal(strip("hey loomy what is the time", "Lumi"), "what is the time");
assert.equal(strip("Hey Lumi", "Lumi"), "");
assert.equal(strip("ஹே லூமி Revit திற", "Lumi"), "Revit திற");
assert.equal(strip("open Revit", "Lumi"), null);
assert.equal(strip("luminous paint", "Lumi"), null);

// Gesture recognition
const flags = (pose, s) => { const r = read(hand(pose, 0.5, 0.5, s)); return [r.pinch, r.fist, r.open]; };
assert.deepEqual(flags("open"), [false, false, true]);
assert.deepEqual(flags("fist"), [false, true, false]);
assert.deepEqual(flags("pinch"), [true, false, false]);
assert.deepEqual(flags("pinch", 0.04), [true, false, false]);          // a small, distant hand

// Controller
const c = new Controller(), yaw0 = c.yaw;
const near = (a, b) => Math.abs(a - b) < 1e-6;
c.update([hand("pinch", 0.4, 0.5)], 0.016); c.update([hand("pinch", 0.5, 0.5)], 0.016);
assert.ok(near(c.yaw, yaw0 + 0.5) && c.mode === "rotate");
c.update([hand("open", 0.5, 0.5)], 0.016);
c.update([hand("pinch", 0.9, 0.5)], 0.016);
assert.ok(near(c.yaw, yaw0 + 0.5), "a new pinch elsewhere must not make the model jump");
c.update([hand("fist", 0.5, 0.5)], 0.016); c.update([hand("fist", 0.6, 0.4)], 0.016);
assert.ok(near(c.x, 0.1) && near(c.y, -0.1));
c.update([hand("pinch", 0.4, 0.5), hand("pinch", 0.6, 0.5)], 0.016);
c.update([hand("pinch", 0.3, 0.5), hand("pinch", 0.7, 0.5)], 0.016);
assert.ok(near(c.scale, 2));
for (let i = 0; i < 120; i++) c.update([hand("open", 0.15, 0.5), hand("open", 0.85, 0.5)], 0.016);
assert.ok(c.explode > 0.99);
for (let i = 0; i < 120; i++) c.update([hand("open", 0.42, 0.5), hand("open", 0.58, 0.5)], 0.016);
assert.ok(c.explode < 0.01);
c.reset(); assert.equal(c.scale, 1);
console.log("web tests passed");
