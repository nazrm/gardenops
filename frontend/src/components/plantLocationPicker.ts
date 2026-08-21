import type { Plant } from "../core/models";
import { t } from "../core/i18n";
import { createModal } from "./dialogCore";

export type PlantLocationDestinationKind = "ground" | "indoor" | "container";

export interface PlantLocationDestination {
  plot_id: string;
  label: string;
  group: string;
  kind: PlantLocationDestinationKind;
  existing_quantity?: number | null;
  plant_already_here?: boolean;
}

export interface PlantLocationPickerOptions {
  mode: "place" | "move";
  plant: Plant;
  sourceLabel?: string;
  sourceQuantity?: number;
  destinations: PlantLocationDestination[];
  getDestinationQuantity?: (plotId: string) => Promise<number | null>;
  onConfirm: (destination: PlantLocationDestination, quantity: number) => Promise<void>;
}

function makeButton(className: string, label: string): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  return button;
}

export function openPlantLocationPicker(options: PlantLocationPickerOptions): void {
  const title = options.mode === "move" ? t("map.move_plant") : t("map.place_plant");
  const { dialog, close } = createModal(
    title,
    '<div class="modal-content plant-location-picker"></div>',
  );
  const content = dialog.querySelector<HTMLElement>(".plant-location-picker");
  if (!content) return;

  const heading = document.createElement("h3");
  heading.textContent = title;
  const plantName = document.createElement("p");
  plantName.className = "plant-location-picker-plant";
  plantName.textContent = options.plant.name;
  content.append(heading, plantName);

  if (options.mode === "move" && options.sourceLabel) {
    const source = document.createElement("p");
    source.className = "plant-location-picker-source";
    source.textContent = t("map.move_from", { location: options.sourceLabel });
    content.appendChild(source);
  }

  const search = document.createElement("input");
  search.type = "search";
  search.className = "plant-location-picker-search";
  search.placeholder = t("map.location_search_placeholder");
  search.setAttribute("aria-label", t("map.location_search_placeholder"));
  content.appendChild(search);

  const list = document.createElement("div");
  list.className = "plant-location-picker-list";
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-label", t("map.location_destinations"));
  content.appendChild(list);

  const quantity = document.createElement("input");
  quantity.type = "number";
  quantity.className = "plant-location-picker-quantity";
  quantity.min = "1";
  quantity.max = String(Math.max(1, options.sourceQuantity ?? options.plant.quantity ?? 1));
  quantity.value = String(options.sourceQuantity && options.sourceQuantity > 1 ? options.sourceQuantity : 1);
  quantity.inputMode = "numeric";
  const quantityField = document.createElement("label");
  quantityField.className = "plant-location-picker-quantity-field";
  const quantityLabel = document.createElement("span");
  quantityLabel.textContent = t("map.location_quantity");
  quantityField.append(quantityLabel, quantity);
  if (options.mode === "move" && (options.sourceQuantity ?? 1) > 1) {
    content.appendChild(quantityField);
  }

  const merge = document.createElement("p");
  merge.className = "plant-location-picker-merge";
  merge.setAttribute("role", "status");
  merge.setAttribute("aria-live", "polite");
  content.appendChild(merge);

  const live = document.createElement("p");
  live.className = "plant-location-picker-status";
  live.setAttribute("role", "status");
  live.setAttribute("aria-live", "polite");
  content.appendChild(live);

  const actions = document.createElement("div");
  actions.className = "plant-location-picker-actions";
  const cancel = makeButton("confirm-no", t("common.cancel"));
  const confirm = makeButton(
    "confirm-yes",
    options.mode === "move" ? t("map.move_here") : t("map.place_here"),
  );
  confirm.disabled = true;
  actions.append(cancel, confirm);
  content.appendChild(actions);

  let selected: PlantLocationDestination | null = null;
  let selectedExistingQuantity: number | null = null;
  let quantityRequest = 0;
  let busy = false;

  function normalizedQuantity(): number {
    const max = Math.max(1, options.sourceQuantity ?? options.plant.quantity ?? 1);
    const parsed = Number.parseInt(quantity.value, 10);
    if (!Number.isFinite(parsed)) return 1;
    return Math.min(max, Math.max(1, parsed));
  }

  function updateMergeText(): void {
    merge.textContent = "";
    if (!selected || selectedExistingQuantity == null || selectedExistingQuantity <= 0) return;
    merge.textContent = t("map.location_merge", {
      existing: selectedExistingQuantity,
      added: normalizedQuantity(),
      total: selectedExistingQuantity + normalizedQuantity(),
    });
  }

  function renderDestinations(): void {
    const query = search.value.trim().toLocaleLowerCase();
    const visible = options.destinations.filter((destination) => {
      if (!query) return true;
      return `${destination.label} ${destination.group}`.toLocaleLowerCase().includes(query);
    });
    list.replaceChildren();
    if (visible.length === 0) {
      const empty = document.createElement("p");
      empty.className = "map-object-empty";
      empty.textContent = t("map.location_no_matches");
      list.appendChild(empty);
      return;
    }

    const groups = new Map<string, PlantLocationDestination[]>();
    for (const destination of visible) {
      const group = groups.get(destination.group) ?? [];
      group.push(destination);
      groups.set(destination.group, group);
    }
    for (const [groupName, destinations] of groups) {
      const group = document.createElement("section");
      group.className = "plant-location-picker-group";
      const heading = document.createElement("h4");
      heading.textContent = groupName;
      group.appendChild(heading);
      for (const destination of destinations) {
        const button = makeButton(
          "plant-location-picker-option",
          destination.label,
        );
        button.dataset["plotId"] = destination.plot_id;
        button.setAttribute("role", "option");
        button.setAttribute("aria-selected", String(selected?.plot_id === destination.plot_id));
        button.classList.toggle("selected", selected?.plot_id === destination.plot_id);
        button.addEventListener("click", () => {
          selected = destination;
          selectedExistingQuantity = destination.existing_quantity ?? null;
          confirm.disabled = true;
          updateMergeText();
          renderDestinations();
          const needsLookup = destination.plant_already_here
            && options.getDestinationQuantity
            && destination.existing_quantity == null;
          if (!needsLookup) {
            confirm.disabled = busy;
            return;
          }
          const request = ++quantityRequest;
          live.textContent = t("map.location_checking");
          void options.getDestinationQuantity!(destination.plot_id).then((value) => {
            if (request !== quantityRequest || selected?.plot_id !== destination.plot_id) return;
            selectedExistingQuantity = value;
            updateMergeText();
            confirm.disabled = busy;
            live.textContent = "";
          }).catch(() => {
            if (request !== quantityRequest) return;
            selectedExistingQuantity = null;
            confirm.disabled = busy;
            live.textContent = "";
          });
        });
        group.appendChild(button);
      }
      list.appendChild(group);
    }
  }

  search.addEventListener("input", renderDestinations);
  quantity.addEventListener("input", updateMergeText);
  cancel.addEventListener("click", close);
  confirm.addEventListener("click", () => {
    if (!selected || busy) return;
    busy = true;
    confirm.disabled = true;
    cancel.disabled = true;
    search.disabled = true;
    quantity.disabled = true;
    live.textContent = t("map.location_saving");
    void options.onConfirm(selected, normalizedQuantity()).then(() => {
      live.textContent = t("map.location_saved");
      close();
    }).catch(() => {
      busy = false;
      confirm.disabled = false;
      cancel.disabled = false;
      search.disabled = false;
      quantity.disabled = false;
      live.textContent = t("map.location_save_failed");
    });
  });

  renderDestinations();
  search.focus();
}
