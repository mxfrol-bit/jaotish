import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

// No browser or GPU dependency: exercise scheduling and context recovery with a GPU double.
function scene() {
  class Element {
    constructor() {
      this.events = new Map();
      this.dataset = {};
      this.hidden = false;
      this.value = "personality";
      const classes = new Set();
      this.classList = {
        add: (c) => classes.add(c),
        remove: (c) => classes.delete(c),
        toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
        contains: (c) => classes.has(c),
      };
    }
    addEventListener(name, cb) {
      const a = this.events.get(name) || [];
      a.push(cb);
      this.events.set(name, a);
    }
    fire(name, event = {}) {
      for (const cb of this.events.get(name) || []) cb(event);
    }
    setAttribute(name, value) {
      this[name] = value;
    }
    replaceChildren() {}
    getBoundingClientRect() {
      return { top: 0, left: 0, width: 1200, height: 720 };
    }
  }
  const selectors = [
    "#experience",
    ".signal-stage",
    "#signal-canvas",
    ".motion-toggle",
    "#topic-start",
    "#topic-description",
    "#analysis-type",
    ".signal-hint",
  ];
  const els = Object.fromEntries(selectors.map((s) => [s, new Element()]));
  const buttons = Array.from({ length: 4 }, () => new Element());
  const doc = new Element();
  doc.hidden = false;
  doc.querySelector = (s) => els[s];
  doc.getElementById = (id) => els["#" + id];
  doc.querySelectorAll = (s) => (s === "[data-signal-topic]" ? buttons : []);
  doc.createElement = () => new Element();
  doc.createTextNode = (text) => ({ text });
  const win = new Element();
  win.scrollY = 0;
  const frames = new Map();
  let nextFrame = 0,
    time = 0,
    programs = 0,
    draws = 0;
  const values = {};
  const gl = new Proxy(
    {},
    {
      get(_, name) {
        if (/^[A-Z_]+$/.test(name)) return name;
        if (name === "createProgram") return () => ({ number: ++programs });
        if (name === "createShader" || name === "createBuffer")
          return () => ({});
        if (name === "getShaderParameter" || name === "getProgramParameter")
          return () => true;
        if (name === "getAttribLocation") return () => 0;
        if (name === "getUniformLocation") return (_, n) => n;
        if (name.startsWith("uniform"))
          return (n, ...args) => {
            values[n] = Array.from(args, (a) =>
              ArrayBuffer.isView(a) ? Array.from(a) : a,
            );
          };
        if (name === "drawArrays") return () => draws++;
        return () => {};
      },
    },
  );
  els["#signal-canvas"].getContext = () => gl;
  class IntersectionObserver {
    constructor(cb) {
      this.cb = cb;
    }
    observe() {
      this.cb([{ isIntersecting: true }]);
    }
    unobserve() {}
  }
  class ResizeObserver {
    constructor(cb) {
      this.cb = cb;
    }
    observe() {
      this.cb();
    }
  }
  const storage = new Map();
  const context = {
    document: doc,
    window: win,
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    IntersectionObserver,
    ResizeObserver,
    localStorage: {
      getItem: (k) => storage.get(k),
      setItem: (k, v) => storage.set(k, v),
    },
    devicePixelRatio: 1,
    scrollY: 0,
    requestAnimationFrame: (cb) => {
      frames.set(++nextFrame, cb);
      return nextFrame;
    },
    cancelAnimationFrame: (id) => frames.delete(id),
    makeSignal: () => ({
      count: 1,
      forms: Array.from({ length: 4 }, () => new Float32Array(3)),
      grain: new Float32Array(2),
    }),
  };
  const source = readFileSync(
    new URL("../app/static/signal.js", import.meta.url),
    "utf8",
  ).replace(/^import[^\n]+\n/, "");
  vm.runInNewContext(source, context);
  function step() {
    const entry = frames.entries().next().value;
    if (!entry) return;
    frames.delete(entry[0]);
    entry[1]((time += 16));
  }
  return {
    els,
    doc,
    step,
    frames,
    values,
    get programs() {
      return programs;
    },
    get draws() {
      return draws;
    },
  };
}

test("pausing preserves shader state and leaves no continuous frame loop", () => {
  const s = scene();
  s.step();
  s.step();
  s.step();
  const before = JSON.stringify(s.values);
  s.els[".motion-toggle"].fire("click");
  s.step();
  assert.equal(JSON.stringify(s.values), before);
  assert.equal(s.frames.size, 0);
  assert.equal(s.els[".signal-stage"].dataset.motion, "paused");
  s.els[".motion-toggle"].fire("click");
  s.step();
  assert.equal(s.frames.size, 1);
});
test("background tabs stop rendering and resume when visible", () => {
  const s = scene();
  s.step();
  s.doc.hidden = true;
  s.doc.fire("visibilitychange");
  assert.equal(s.frames.size, 0);
  s.doc.hidden = false;
  s.doc.fire("visibilitychange");
  s.step();
  assert.equal(s.frames.size, 1);
});
test("context loss shows SVG and restoration rebuilds GPU resources", () => {
  const s = scene();
  s.step();
  s.els["#signal-canvas"].fire("webglcontextlost", { preventDefault() {} });
  assert.equal(s.els[".signal-stage"].dataset.renderer, "svg");
  assert.equal(s.frames.size, 0);
  const before = s.programs;
  s.els["#signal-canvas"].fire("webglcontextrestored");
  s.step();
  assert.equal(s.programs, before + 1);
  assert.equal(s.els[".signal-stage"].dataset.renderer, "webgl");
  assert.equal(s.frames.size, 1);
});
