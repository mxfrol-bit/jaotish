import { makeSignal } from "./signal-geometry.mjs?v=20260914d";

const experience = document.getElementById("experience");
const stage = document.querySelector(".signal-stage");
const canvas = document.getElementById("signal-canvas");
const reduce = matchMedia("(prefers-reduced-motion: reduce)");
const narrow = matchMedia("(max-width: 600px)");
const fine = matchMedia("(hover: hover) and (pointer: fine)");
const pause = document.querySelector(".motion-toggle");
const topicStart = document.getElementById("topic-start");
const copy = [
  [
    "personality",
    "Сильные стороны, привычные реакции и то, на что можно опереться.",
    "Разобрать мой характер",
  ],
  [
    "relationships",
    "Близость, общение и сценарии, которые повторяются в отношениях.",
    "Разобрать отношения",
  ],
  [
    "work",
    "Способности, мотивация и формат работы, в котором хочется развиваться.",
    "Найти своё направление",
  ],
  [
    "current_period",
    "Темы текущего месяца и вопросы, которым хочется уделить внимание.",
    "Разобрать мой период",
  ],
];
let selected = 0;
let requestDraw = () => {};
document.querySelectorAll("[data-signal-topic]").forEach((button) => {
  button.addEventListener("click", () => {
    selected = Number(button.dataset.signalTopic);
    document.querySelectorAll("[data-signal-topic]").forEach((item) => {
      const on = item === button;
      item.classList.toggle("is-active", on);
      item.setAttribute("aria-pressed", String(on));
    });
    const [topic, description, label] = copy[selected];
    document.getElementById("topic-description").textContent = description;
    topicStart.dataset.topic = topic;
    topicStart.replaceChildren(document.createTextNode(label + " "));
    const arrow = document.createElement("span");
    arrow.setAttribute("aria-hidden", "true");
    arrow.textContent = "↗";
    topicStart.append(arrow);
    requestDraw();
  });
});

// Reveal only after the observer is ready, so a failed enhancement cannot hide content.
if ("IntersectionObserver" in window && !reduce.matches) {
  const entrances = new IntersectionObserver(
    (entries) => {
      for (const entry of entries)
        if (entry.isIntersecting) {
          entry.target.classList.add("in-view");
          entrances.unobserve(entry.target);
        }
    },
    { threshold: 0.08 },
  );
  document
    .querySelectorAll(
      ".example-copy,.sample-report,.how .section-heading,.how-steps,.form-intro",
    )
    .forEach((el) => {
      entrances.observe(el);
      el.classList.add("reveal-ready");
    });
}

function initSurface() {
  if (!canvas || !experience || !stage) return;
  const gl = canvas.getContext("webgl", {
    alpha: true,
    antialias: false,
    powerPreference: "low-power",
  });
  if (!gl) return;
  const vertex = `
    precision highp float;
    attribute vec3 a0; attribute vec3 a1; attribute vec3 a2; attribute vec3 a3;
    attribute vec2 grain;
    uniform vec4 weights;
    uniform vec2 size;
    uniform vec2 pointer;
    uniform vec2 tilt;
    uniform float time;
    uniform float scroll;
    uniform float energy;
    uniform float ratio;
    uniform float small;
    uniform float moving;
    varying float light;
    void main(){
      vec3 p = a0*weights.x+a1*weights.y+a2*weights.z+a3*weights.w;
      float t=time*.27;
      p.z += sin(p.y*4.0+p.x*2.4+t)*.07*moving;
      p.x += sin(p.y*2.7)*scroll*.18;
      p.z += cos(p.y*3.2+p.x)*scroll*.16;
      p.x += sin(grain.x*48.0+time*.7)*energy*.14;
      p.y += cos(grain.x*34.0+time*.5)*energy*.10;
      float turn=-.43+tilt.x*.22+sin(t*.38)*.10*moving+scroll*.20;
      float lean=.11+tilt.y*.11;
      p.xz=mat2(cos(turn),-sin(turn),sin(turn),cos(turn))*p.xz;
      p.yz=mat2(cos(lean),-sin(lean),sin(lean),cos(lean))*p.yz;
      float perspective=3.4/(3.4-p.z);
      float scale=mix(min(size.y*.39,size.x*.34),min(size.y*.255,size.x*.60),small);
      vec2 center=vec2(size.x*mix(.735,.68,small),size.y*mix(.49,.30,small));
      vec2 screen=center+vec2(p.x,-p.y)*scale*perspective;
      vec2 delta=screen-pointer;
      float dist=length(delta);
      float influence=exp(-dist*dist/(scale*scale*.08))*moving;
      screen += normalize(delta+vec2(.01))*influence*scale*.12;
      float shine=.52+.48*pow(abs(sin(p.y*2.4+p.x*2.8+time*.08*moving)),5.0);
      light=(.62+grain.x*.88)*grain.y*shine*(.9+p.z*.35)+influence*.30;
      gl_Position=vec4(screen/size*2.0-1.0,0.0,1.0);
      gl_Position.y *= -1.0;
      gl_PointSize=(.8+grain.x*.8+influence*.65)*ratio*perspective;
    }
  `;
  const fragment = `
    precision mediump float;
    varying float light;
    void main(){
      float d=length(gl_PointCoord-.5);
      if(d>.5) discard;
      float edge=1.0-smoothstep(.23,.5,d);
      gl_FragColor=vec4(.80,.88,.93,light*edge);
    }
  `;
  function compile(type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      gl.deleteShader(shader);
      throw new Error("Surface shader unavailable");
    }
    return shader;
  }
  let program, vs, fs;
  try {
    vs = compile(gl.VERTEX_SHADER, vertex);
    fs = compile(gl.FRAGMENT_SHADER, fragment);
    program = gl.createProgram();
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS))
      throw new Error("Surface program unavailable");
  } catch {
    return;
  }
  gl.deleteShader(vs);
  gl.deleteShader(fs);
  gl.useProgram(program);
  const data = makeSignal(96, narrow.matches ? 120 : 220);
  const buffers = [];
  function bind(name, array, components) {
    const buffer = gl.createBuffer();
    buffers.push(buffer);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, array, gl.STATIC_DRAW);
    const location = gl.getAttribLocation(program, name);
    gl.enableVertexAttribArray(location);
    gl.vertexAttribPointer(location, components, gl.FLOAT, false, 0, 0);
  }
  data.forms.forEach((form, i) => bind("a" + i, form, 3));
  bind("grain", data.grain, 2);
  const uniforms = {};
  for (const name of [
    "weights",
    "size",
    "pointer",
    "tilt",
    "time",
    "scroll",
    "energy",
    "ratio",
    "small",
    "moving",
  ])
    uniforms[name] = gl.getUniformLocation(program, name);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
  gl.disable(gl.DEPTH_TEST);
  let width = 0,
    height = 0,
    dpr = 1,
    visible = false,
    paused = false,
    raf = 0,
    last = 0,
    clock = 0,
    lastScroll = window.scrollY,
    scrollEnergy = 0,
    progress = 0;
  let lost = false,
    ready = false;
  const weights = new Float32Array([1, 0, 0, 0]);
  let mouseX = -10000,
    mouseY = -10000,
    tiltX = 0,
    tiltY = 0,
    targetX = 0,
    targetY = 0;
  function resize() {
    const r = stage.getBoundingClientRect();
    width = r.width;
    height = r.height;
    dpr = Math.min(devicePixelRatio || 1, narrow.matches ? 1.5 : 2);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    gl.viewport(0, 0, canvas.width, canvas.height);
    requestDraw();
  }
  function shouldMove() {
    return visible && !document.hidden && !reduce.matches && !paused && !lost;
  }
  function draw(now) {
    raf = 0;
    if (lost || !width || !height) return;
    const moving = shouldMove();
    const dt = last ? Math.min((now - last) / 1000, 0.05) : 0.016;
    last = now;
    if (moving) clock += dt;
    const top = experience.getBoundingClientRect().top;
    progress = Math.max(0, Math.min(1, (-top + height * 0.3) / height));
    const target = progress > 0.68 ? selected : 0;
    const smoothing = moving ? 1 - Math.exp(-dt * 6) : 1;
    for (let i = 0; i < 4; i++)
      weights[i] += ((i === target ? 1 : 0) - weights[i]) * smoothing;
    const delta = Math.abs(scrollY - lastScroll);
    lastScroll = scrollY;
    scrollEnergy += (Math.min(1, delta / 50) - scrollEnergy) * 0.14;
    tiltX += (targetX - tiltX) * smoothing;
    tiltY += (targetY - tiltY) * smoothing;
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform4fv(uniforms.weights, weights);
    gl.uniform2f(uniforms.size, width, height);
    gl.uniform2f(uniforms.pointer, mouseX, mouseY);
    gl.uniform2f(uniforms.tilt, tiltX, tiltY);
    gl.uniform1f(uniforms.time, clock);
    gl.uniform1f(uniforms.scroll, reduce.matches ? 0 : progress);
    gl.uniform1f(uniforms.energy, moving ? scrollEnergy : 0);
    gl.uniform1f(uniforms.ratio, dpr);
    gl.uniform1f(uniforms.small, narrow.matches ? 1 : 0);
    gl.uniform1f(uniforms.moving, moving ? 1 : 0);
    gl.drawArrays(gl.POINTS, 0, data.count);
    if (!ready) {
      ready = true;
      stage.classList.add("is-ready");
      stage.dataset.renderer = "webgl";
      updateHint();
    }
    if (moving) raf = requestAnimationFrame(draw);
  }
  requestDraw = () => {
    if (!raf && !lost && !document.hidden) raf = requestAnimationFrame(draw);
  };
  function sync() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    last = 0;
    if (visible) requestDraw();
    pause.hidden = reduce.matches || lost;
  }
  const observer = new IntersectionObserver(
    (entries) => {
      visible = entries[0].isIntersecting;
      sync();
    },
    { threshold: 0 },
  );
  observer.observe(experience);
  const ro = new ResizeObserver(resize);
  ro.observe(stage);
  experience.addEventListener(
    "pointermove",
    (event) => {
      if (!fine.matches || paused || reduce.matches) return;
      const r = stage.getBoundingClientRect();
      mouseX = event.clientX - r.left;
      mouseY = event.clientY - r.top;
      targetX = (mouseX / width - 0.5) * 2;
      targetY = (mouseY / height - 0.5) * 2;
    },
    { passive: true },
  );
  experience.addEventListener("pointerleave", () => {
    mouseX = mouseY = -10000;
    targetX = targetY = 0;
  });
  // No scroll hijacking. A single pending frame also updates the static paused state.
  window.addEventListener(
    "scroll",
    () => {
      if (visible) requestDraw();
    },
    { passive: true },
  );
  document.addEventListener("visibilitychange", sync);
  reduce.addEventListener("change", sync);
  pause.addEventListener("click", () => {
    paused = !paused;
    pause.setAttribute("aria-pressed", String(paused));
    pause.textContent = paused
      ? "Продолжить движение ▷"
      : "Остановить движение Ⅱ";
    sync();
  });
  canvas.addEventListener("webglcontextlost", (event) => {
    event.preventDefault();
    lost = true;
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    stage.classList.remove("is-ready");
    stage.dataset.renderer = "svg";
    pause.hidden = true;
  });
  canvas.addEventListener("webglcontextrestored", () => {
    // The SVG stays usable; reinitialising is deliberately left to the next navigation.
    stage.dataset.renderer = "svg";
  });
  window.addEventListener("pagehide", () => {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
  });
  window.addEventListener("pageshow", sync);
  resize();
  sync();
}
const hint = document.querySelector(".signal-hint");
function updateHint() {
  hint.textContent =
    reduce.matches || stage?.dataset.renderer !== "webgl"
      ? "Форма личного следа"
      : fine.matches
        ? "Двигай курсор — меняй форму"
        : "Листай — след меняется";
}
updateHint();
fine.addEventListener("change", updateHint);
reduce.addEventListener("change", updateHint);
try {
  initSurface();
} catch {
  stage?.classList.remove("is-ready");
}
