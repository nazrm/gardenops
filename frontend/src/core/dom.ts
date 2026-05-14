/**
 * Typed DOM query helpers that use `instanceof` checks
 * instead of unsafe `as` type assertions.
 */

export function queryInput(id: string): HTMLInputElement | null {
  const el = document.getElementById(id);
  return el instanceof HTMLInputElement ? el : null;
}

export function querySelect(id: string): HTMLSelectElement | null {
  const el = document.getElementById(id);
  return el instanceof HTMLSelectElement ? el : null;
}

export function queryButton(id: string): HTMLButtonElement | null {
  const el = document.getElementById(id);
  return el instanceof HTMLButtonElement ? el : null;
}

export function queryTextArea(id: string): HTMLTextAreaElement | null {
  const el = document.getElementById(id);
  return el instanceof HTMLTextAreaElement ? el : null;
}

export function createFieldGroup(
  label: string,
  groupClass: string,
  labelClass?: string,
): HTMLElement {
  const group = document.createElement("div");
  group.className = groupClass;
  const lbl = document.createElement("label");
  if (labelClass) lbl.className = labelClass;
  lbl.textContent = label;
  group.appendChild(lbl);
  return group;
}

export function createParagraph(
  text: string,
  color?: string,
  fontSize?: string,
): HTMLParagraphElement {
  const p = document.createElement("p");
  p.textContent = text;
  if (color) p.style.color = color;
  if (fontSize) p.style.fontSize = fontSize;
  return p;
}
