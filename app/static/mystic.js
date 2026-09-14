(() => {
  const shell = document.querySelector(".hero-shell");
  if (!shell) return;
  const canvas = document.createElement("canvas");
  canvas.className = "star-canvas";
  canvas.setAttribute("aria-hidden", "true");
  shell.prepend(canvas);
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  let width = 0,
    height = 0,
    stars = [],
    frame = 0,
    visible = true,
    last = 0;
  function build() {
    const ratio = Math.min(devicePixelRatio || 1, 2);
    width = shell.clientWidth;
    height = shell.clientHeight;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    stars = Array.from(
      { length: Math.min(110, Math.round((width * height) / 10000)) },
      (_, i) => ({
        x: (((i * 137.51) % 997) / 997) * width,
        y: (((i * 71.79) % 631) / 631) * height,
        r: 0.3 + (i % 5) * 0.13,
        a: 0.15 + (i % 7) * 0.045,
        phase: i * 1.7,
      }),
    );
    draw(0);
  }
  function draw(t) {
    ctx.clearRect(0, 0, width, height);
    for (const s of stars) {
      ctx.fillStyle = `rgba(214,205,184,${s.a * (motion.matches ? 1 : 0.7 + 0.3 * Math.sin(t * 0.00065 + s.phase))})`;
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  function tick(t) {
    frame = 0;
    if (!visible || document.hidden || motion.matches) return;
    if (t - last > 45) {
      draw(t);
      last = t;
    }
    frame = requestAnimationFrame(tick);
  }
  function resume() {
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    if (motion.matches) draw(0);
    else if (visible && !document.hidden) frame = requestAnimationFrame(tick);
  }
  const observer = new IntersectionObserver((entries) => {
    visible = entries[0].isIntersecting;
    resume();
  });
  observer.observe(shell);
  window.addEventListener("resize", build);
  document.addEventListener("visibilitychange", resume);
  motion.addEventListener("change", resume);
  build();
  resume();
})();
