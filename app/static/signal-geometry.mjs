// One material, four authored surfaces. Shared by WebGL and the SVG fallback.
export function makeSignal(rows = 96, columns = 220) {
  const count = rows * columns;
  const forms = Array.from({ length: 4 }, () => new Float32Array(count * 3));
  const grain = new Float32Array(count * 2);
  for (let row = 0; row < rows; row++) {
    const u = (row / (rows - 1)) * 2 - 1;
    for (let col = 0; col < columns; col++) {
      const v = (col / (columns - 1)) * 2 - 1;
      const i = row * columns + col;
      const noise = Math.sin(i * 127.1 + 43.7) * 43758.5453;
      const seed = noise - Math.floor(noise);
      const edge = Math.sqrt(Math.max(0, 1 - v * v)) * 0.25 + 0.62;
      // A ridged impression folded along its own axis, with a cut lower edge.
      const x = u * edge + 0.2 * Math.sin(v * 2.7 + u * 0.7);
      const y = v * 1.17 + 0.12 * Math.sin(u * 2.8) * (1 - v * v);
      const z = 0.4 * Math.cos(u * 2.7 + v * 1.2) + 0.17 * Math.sin(v * 4.1);
      forms[0].set([x, y, z], i * 3);
      // Two connected strips: proximity and separation without an infinity icon.
      const side = row < rows / 2 ? -1 : 1;
      const band = ((row % (rows / 2)) / (rows / 2 - 1) - 0.5) * 0.5;
      const angle = v * 2.25 + side * 0.5;
      forms[1].set(
        [
          side * 0.34 + Math.sin(angle) * 0.33 + band * Math.cos(angle),
          v * 1.18,
          0.44 * Math.cos(angle) * side + band * Math.sin(angle),
        ],
        i * 3,
      );
      // An open fan of trajectories, not a closed orbit.
      const a = u * 1.0 + 0.1;
      const length = 0.22 + (v + 1) * 0.57;
      forms[2].set(
        [
          Math.sin(a) * length + 0.17 * v,
          Math.cos(a) * length - 0.7,
          0.27 * Math.sin(u * 3 + v * 2.4),
        ],
        i * 3,
      );
      // Interleaving wave fronts, time as layers rather than a clock face.
      const wave = Math.sin(v * 3.2 + u * 1.6);
      forms[3].set(
        [
          u * 0.91,
          v * 0.78 + 0.13 * Math.cos(u * 3),
          wave * 0.44 + 0.16 * Math.sin(u * 4 - v),
        ],
        i * 3,
      );
      grain.set([seed, 0.6 + 0.4 * Math.cos(u * 2.8 + v * 0.8) ** 2], i * 2);
    }
  }
  return { count, forms, grain };
}
