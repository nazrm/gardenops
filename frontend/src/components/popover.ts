import type { Plant } from "../core/models";
import { t } from "../core/i18n";

export interface PopoverParams {
  plotId: string;
  zone: string;
  plantCount: number;
  plants: Plant[];
  anchorRect: DOMRect;
  viewportRect: DOMRect;
  onViewDetails: () => void;
  onEdit?: (() => void) | undefined;
  onDismiss: () => void;
}

let activePopover: HTMLElement | null = null;
let activeCleanup: (() => void) | null = null;

export function showPopover(params: PopoverParams): void {
  dismissPopover();

  const {
    plotId, zone, plantCount, plants,
    anchorRect, viewportRect,
    onViewDetails, onEdit, onDismiss,
  } = params;

  const el = document.createElement("div");
  el.className = "plot-popover";

  const header = document.createElement("div");
  header.className = "popover-header";

  const title = document.createElement("strong");
  title.textContent = plotId;

  const zoneChip = document.createElement("span");
  zoneChip.className = "popover-zone";
  zoneChip.dataset["zone"] = zone;
  zoneChip.textContent = zone;

  header.append(title, zoneChip);

  const meta = document.createElement("div");
  meta.className = "popover-meta";
  meta.textContent = t("popover.plant_count", { count: plantCount });

  const plantList = document.createElement("ul");
  plantList.className = "popover-plants";
  if (plants.length > 0) {
    plants.forEach((plant) => {
      const item = document.createElement("li");
      item.textContent = plant.name;
      plantList.appendChild(item);
    });
  } else {
    const item = document.createElement("li");
    item.className = "text-muted";
    item.textContent = t("popover.no_plants");
    plantList.appendChild(item);
  }

  const actions = document.createElement("div");
  actions.className = "popover-actions";

  const detailsBtn = document.createElement("button");
  detailsBtn.className = "popover-details-btn";
  detailsBtn.type = "button";
  detailsBtn.dataset["viewPlotDetails"] = plotId;
  detailsBtn.textContent = t("popover.view_details");
  detailsBtn.addEventListener("click", () => {
    dismissPopover();
    onViewDetails();
  });

  actions.append(detailsBtn);
  if (onEdit) {
    const editBtn = document.createElement("button");
    editBtn.className = "popover-edit-btn";
    editBtn.type = "button";
    editBtn.title = t("popover.edit_plot");
    editBtn.textContent = "\u270E";
    editBtn.addEventListener("click", () => {
      dismissPopover();
      onEdit();
    });
    actions.appendChild(editBtn);
  }
  el.append(header, meta, plantList, actions);

  document.body.appendChild(el);
  activePopover = el;

  positionPopover(el, anchorRect, viewportRect);

  const removeListeners = () => {
    document.removeEventListener("click", onClickOutside);
    window.removeEventListener("keydown", onEscape);
  };

  const onClickOutside = (e: MouseEvent) => {
    if (!el.contains(e.target as Node)) {
      dismissPopover();
      onDismiss();
    }
  };
  const onEscape = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      dismissPopover();
      onDismiss();
    }
  };

  activeCleanup = removeListeners;

  setTimeout(() => {
    document.addEventListener("click", onClickOutside);
  }, 50);
  window.addEventListener("keydown", onEscape);
}

function positionPopover(
  el: HTMLElement,
  anchor: DOMRect,
  viewport: DOMRect,
): void {
  const gap = 8;
  let left = anchor.right + gap;
  let top = anchor.top;

  const popW = el.offsetWidth || 260;
  const popH = el.offsetHeight || 240;

  if (left + popW > viewport.right - 8) {
    left = anchor.left - popW - gap;
  }
  if (left < viewport.left + 8) {
    left = viewport.left + 8;
  }
  if (top + popH > viewport.bottom - 8) {
    top = viewport.bottom - popH - 8;
  }
  if (top < viewport.top + 8) {
    top = viewport.top + 8;
  }

  el.style.left = `${left}px`;
  el.style.top = `${top}px`;
}

export function dismissPopover(): void {
  if (activeCleanup) {
    activeCleanup();
    activeCleanup = null;
  }
  if (activePopover) {
    activePopover.remove();
    activePopover = null;
  }
}
