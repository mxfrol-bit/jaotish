/* Animate the single signature illustration only when it can be seen. */
(() => {
  const art = document.querySelector(".hero-art");
  if (!art) return;
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  let visible = false;
  function update() {
    art.classList.toggle(
      "art-awake",
      visible && !document.hidden && !motion.matches,
    );
  }
  new IntersectionObserver(
    (entries) => {
      visible = entries[0].isIntersecting;
      update();
    },
    { threshold: 0.12 },
  ).observe(art);
  document.addEventListener("visibilitychange", update);
  motion.addEventListener("change", update);
})();
