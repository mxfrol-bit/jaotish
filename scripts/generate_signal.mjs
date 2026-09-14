import { makeSignal } from "../app/static/signal-geometry.mjs";
import { writeFileSync } from "node:fs";
const { count, forms, grain } = makeSignal(72, 180);
const groups = Array.from({ length: 5 }, () => []);
for (let i = 0; i < count; i++) {
  const [x, y, z] = forms[0].slice(i * 3, i * 3 + 3);
  const turn = -0.43,
    px = x * Math.cos(turn) + z * Math.sin(turn),
    pz = -x * Math.sin(turn) + z * Math.cos(turn);
  const depth = 3.4 / (3.4 - pz),
    sx = 400 + px * 360 * depth,
    sy = 500 - y * 360 * depth;
  groups[Math.floor(grain[i * 2] * 4.99)].push(
    `M${sx.toFixed(1)} ${sy.toFixed(1)}h.01`,
  );
}
const paths = groups
  .map(
    (items, i) =>
      `<path opacity="${(0.26 + i * 0.13).toFixed(2)}" stroke-width="${(1 + i * 0.18).toFixed(2)}" d="${items.join("")}"/>`,
  )
  .join("");
writeFileSync(
  new URL("../app/static/signal/trace.svg", import.meta.url),
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 1000"><title>Личный след</title><g fill="none" stroke="#cbdce7" stroke-linecap="round">${paths}</g></svg>`,
);
console.log("Vector fallback:", count, "particles");
