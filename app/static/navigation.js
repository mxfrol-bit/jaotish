/* Native details menu remains usable if enhancement is unavailable. */

const menu = document.querySelector(".mobile-menu");
if (menu) {
  const summary = menu.querySelector("summary");
  menu.addEventListener("click", (event) => {
    if (event.target.closest("a")) menu.open = false;
  });
  menu.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && menu.open) {
      menu.open = false;
      summary.focus();
    }
  });
  document.addEventListener("pointerdown", (event) => {
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  });
  matchMedia("(min-width: 801px)").addEventListener("change", (event) => {
    if (event.matches) menu.open = false;
  });
}
