import type { MapObject, MapObjectType, MapObjectUnitType } from "../core/models";
import { t } from "../core/i18n";

interface RenderMapObjectsPanelParams {
  container: HTMLElement | null;
  objects: MapObject[];
  selectedObjectId: string | null;
  showObjects: boolean;
  canWrite: boolean;
  selectedPlotCount: number;
  onToggleObjects: (show: boolean) => void;
  onCreateObject: (type: MapObjectType) => void;
  onSelectObject: (publicId: string | null) => void;
  onDeleteObject: (publicId: string) => void;
  onAddUnit: (objectPublicId: string, type: MapObjectUnitType) => void;
  onDeleteUnit: (objectPublicId: string, unitPublicId: string) => void;
}

function objectTypeLabel(type: MapObjectType): string {
  switch (type) {
    case "patio": return t("map.object_patio");
    case "terrace": return t("map.object_terrace");
    case "greenhouse": return t("map.object_greenhouse");
    case "shed": return t("map.object_shed");
    case "pond": return t("map.object_pond");
    case "path": return t("map.object_path");
    case "bed": return t("map.object_bed");
    default: return t("map.object_other");
  }
}

function unitTypeLabel(type: MapObjectUnitType): string {
  switch (type) {
    case "pot": return t("map.unit_pot");
    case "planter": return t("map.unit_planter");
    case "raised_bed": return t("map.unit_raised_bed");
    case "shelf": return t("map.unit_shelf");
    default: return t("map.unit_other");
  }
}

function makeButton(className: string, label: string, title = label): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.title = title;
  return button;
}

function buildCreateRow(params: RenderMapObjectsPanelParams): HTMLElement {
  const row = document.createElement("div");
  row.className = "map-object-create-row";

  const patio = makeButton(
    "cat-filter-btn",
    `+ ${t("map.object_patio")}`,
    params.selectedPlotCount > 0
      ? t("map.object_create_from_selection")
      : t("map.object_create_default"),
  );
  patio.disabled = !params.canWrite;
  patio.id = "map-object-create-patio-btn";
  patio.addEventListener("click", () => params.onCreateObject("patio"));

  const terrace = makeButton("cat-filter-btn", `+ ${t("map.object_terrace")}`);
  terrace.disabled = !params.canWrite;
  terrace.id = "map-object-create-terrace-btn";
  terrace.addEventListener("click", () => params.onCreateObject("terrace"));

  row.append(patio, terrace);
  return row;
}

function buildObjectList(params: RenderMapObjectsPanelParams): HTMLElement {
  const list = document.createElement("div");
  list.className = "map-object-list";

  if (params.objects.length === 0) {
    const empty = document.createElement("p");
    empty.className = "map-object-empty";
    empty.textContent = t("map.object_empty");
    list.appendChild(empty);
    return list;
  }

  params.objects.forEach((object) => {
    const row = document.createElement("div");
    row.className = "map-object-row";
    row.dataset["objectId"] = object.public_id;

    const select = makeButton("map-object-row-main", object.name);
    select.classList.toggle("active", object.public_id === params.selectedObjectId);
    select.addEventListener("click", () => {
      params.onSelectObject(
        object.public_id === params.selectedObjectId ? null : object.public_id,
      );
    });

    const meta = document.createElement("span");
    meta.className = "map-object-row-meta";
    meta.textContent = `${objectTypeLabel(object.object_type)} · ${object.geometry.width}×${object.geometry.height}`;
    select.appendChild(meta);

    const del = makeButton("map-object-icon-btn", "×", t("common.delete"));
    del.disabled = !params.canWrite;
    del.addEventListener("click", () => params.onDeleteObject(object.public_id));

    row.append(select, del);
    list.appendChild(row);
  });

  return list;
}

function buildUnitGrid(
  object: MapObject,
  params: RenderMapObjectsPanelParams,
): HTMLElement {
  const grid = document.createElement("div");
  grid.className = "map-object-unit-grid";
  grid.style.setProperty("--unit-rows", String(object.internal_layout.rows));
  grid.style.setProperty("--unit-cols", String(object.internal_layout.cols));

  object.units.forEach((unit) => {
    const cell = document.createElement("button");
    cell.type = "button";
    cell.className = `map-object-unit map-object-unit--${unit.shape_type}`;
    cell.style.gridRow = `${unit.geometry.y} / ${unit.geometry.y + unit.geometry.height}`;
    cell.style.gridColumn = `${unit.geometry.x} / ${unit.geometry.x + unit.geometry.width}`;
    cell.style.setProperty("--map-object-unit-color", unit.style.color);
    cell.textContent = unit.name;
    cell.title = `${unit.name} · ${unitTypeLabel(unit.unit_type)}`;
    cell.setAttribute("aria-label", `${t("common.delete")} ${unit.name}`);
    cell.disabled = !params.canWrite;
    cell.addEventListener("click", () => {
      params.onDeleteUnit(object.public_id, unit.public_id);
    });
    grid.appendChild(cell);
  });

  return grid;
}

function buildSelectedObject(params: RenderMapObjectsPanelParams): HTMLElement | null {
  const selected = params.objects.find((object) => object.public_id === params.selectedObjectId);
  if (!selected) return null;

  const panel = document.createElement("div");
  panel.className = "map-object-detail";

  const heading = document.createElement("div");
  heading.className = "map-object-detail-heading";
  const name = document.createElement("strong");
  name.textContent = selected.name;
  const status = document.createElement("span");
  status.textContent = t("map.object_layout_only");
  heading.append(name, status);

  const actions = document.createElement("div");
  actions.className = "map-object-create-row";
  const pot = makeButton("cat-filter-btn", `+ ${t("map.unit_pot")}`);
  pot.disabled = !params.canWrite;
  pot.addEventListener("click", () => params.onAddUnit(selected.public_id, "pot"));
  const planter = makeButton("cat-filter-btn", `+ ${t("map.unit_planter")}`);
  planter.disabled = !params.canWrite;
  planter.addEventListener("click", () => params.onAddUnit(selected.public_id, "planter"));
  actions.append(pot, planter);

  panel.append(heading, actions, buildUnitGrid(selected, params));
  return panel;
}

export function renderMapObjectsPanel(params: RenderMapObjectsPanelParams): void {
  const { container } = params;
  if (!container) return;

  const header = document.createElement("div");
  header.className = "map-layer-section-header";
  const title = document.createElement("h3");
  title.textContent = t("map.objects");
  const toggle = makeButton(
    "map-object-toggle",
    params.showObjects ? t("map.object_hide") : t("map.object_show"),
  );
  toggle.classList.toggle("active", params.showObjects);
  toggle.addEventListener("click", () => params.onToggleObjects(!params.showObjects));
  header.append(title, toggle);

  const selectedPanel = buildSelectedObject(params);
  container.replaceChildren(
    header,
    buildCreateRow(params),
    buildObjectList(params),
    ...(selectedPanel ? [selectedPanel] : []),
  );
}
