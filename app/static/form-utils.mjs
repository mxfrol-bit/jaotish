// Keep date entry usable on numeric phone keyboards and during edits/paste.
export function formatBirthDateInput(raw, caret = raw.length) {
  const iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(raw.trim());
  if (iso) return { text: `${iso[3]}.${iso[2]}.${iso[1]}`, caret: 10 };
  // Preserve existing date segments while the user corrects the day or month.
  if (/^\d{0,2}\.\d{0,2}\.\d{0,4}$/.test(raw))
    return { text: raw, caret: Math.min(caret, raw.length) };
  const digits = raw.replace(/\D/g, "").slice(0, 8);
  const before = Math.min(raw.slice(0, caret).replace(/\D/g, "").length, 8);
  const text = [digits.slice(0, 2), digits.slice(2, 4), digits.slice(4)]
    .filter(Boolean)
    .join(".");
  let position = 0,
    seen = 0;
  while (position < text.length && seen < before) {
    if (/\d/.test(text[position])) seen++;
    position++;
  }
  return { text, caret: position };
}
