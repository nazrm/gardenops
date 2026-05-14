import type { Plot } from "../core/models";

export interface ZoneToggleZone {
  code: string;
  label: string;
}

export interface ZoneToggleState {
  hiddenZones: Set<string>;
}

export function zoneToggleZonesFromPlots(plots: Plot[]): ZoneToggleZone[] {
  const zones = new Map<string, string>();
  for (const plot of plots) {
    if (plot.grid_row === null || plot.grid_col === null) continue;
    const code = plot.zone_code.trim();
    if (!code || zones.has(code)) continue;
    zones.set(code, plot.zone_name.trim() || code);
  }
  return [...zones.entries()]
    .map(([code, label]) => ({ code, label }))
    .sort((a, b) => a.code.localeCompare(b.code, undefined, {
      numeric: true,
      sensitivity: "base",
    }));
}

export function renderZoneToggles(
  container: HTMLElement,
  zones: ZoneToggleZone[],
  state: ZoneToggleState,
  onToggle: (zone: string) => void,
): void {
  container.replaceChildren();
  for (const zone of zones) {
    const btn = document.createElement("button");
    btn.className = "zone-pill";
    btn.dataset["zone"] = zone.code;
    btn.textContent = zone.code;
    btn.title = zone.label;
    btn.setAttribute(
      "aria-pressed",
      state.hiddenZones.has(zone.code) ? "false" : "true",
    );
    if (state.hiddenZones.has(zone.code)) {
      btn.classList.add("zone-pill-off");
    }
    btn.addEventListener("click", () => onToggle(zone.code));
    container.appendChild(btn);
  }
}

export function applyZoneVisibility(
  grid: HTMLElement,
  hiddenZones: Set<string>,
): void {
  const plots = grid.querySelectorAll<HTMLElement>(".plot[data-zone]");
  plots.forEach((plot) => {
    const zoneCode = plot.dataset["zone"] ?? "";
    plot.style.display = hiddenZones.has(zoneCode) ? "none" : "";
  });
  const ghosts = grid.querySelectorAll<HTMLElement>(".drop-ghost[data-zone]");
  ghosts.forEach((ghost) => {
    const zoneCode = ghost.dataset["zone"] ?? "";
    ghost.style.display = hiddenZones.has(zoneCode) ? "none" : "";
  });
  const house = grid.querySelector<HTMLElement>(".house-block");
  if (house) {
    house.style.display = "";
  }
}
