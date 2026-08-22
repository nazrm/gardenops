import type {
  ContainerPatch,
  ContainerSummary,
  ContainerType,
  MapObject,
  MapObjectGeometry,
  MapObjectInput,
  MapObjectShape,
  MapObjectType,
  Plot,
} from "../core/models";
import { t } from "../core/i18n";

const AREA_TYPES = ["patio", "terrace", "greenhouse", "balcony", "other"] as const;
const CONTAINER_TYPES = ["pot", "planter", "raised_bed", "other"] as const;
const DEFAULT_AREA_COLOR = "#8f9f7d";

export interface MapObjectGeometryConflicts {
  outOfBounds: boolean;
  plotIds: string[];
  objectIds: string[];
  house: boolean;
}

export interface MapObjectGeometryConflictContext {
  gridRows: number;
  gridCols: number;
  plots: readonly Plot[];
  objects: readonly MapObject[];
  housePosition: { row: number; col: number };
  houseSize: { width: number; height: number };
  ignoreObjectId?: string | null;
}

/** Map occupancy uses half-open rectangles so adjacent cells remain valid. */
export function getMapObjectGeometryConflicts(
  geometry: MapObjectGeometry,
  context: MapObjectGeometryConflictContext,
): MapObjectGeometryConflicts {
  const overlaps = (
    left: MapObjectGeometry,
    right: MapObjectGeometry,
  ): boolean => (
    left.x < right.x + right.width
    && right.x < left.x + left.width
    && left.y < right.y + right.height
    && right.y < left.y + left.height
  );

  const outOfBounds = (
    geometry.x < 1
    || geometry.y < 1
    || geometry.x + geometry.width > context.gridCols + 1
    || geometry.y + geometry.height > context.gridRows + 1
  );
  const plotIds: string[] = [];
  for (const plot of context.plots) {
    if (
      plot.archived_at_ms != null
      || plot.plot_kind === "container"
      || plot.container_type != null
      || plot.grid_row == null
      || plot.grid_col == null
    ) {
      continue;
    }
    if (
      overlaps(geometry, {
        x: plot.grid_col,
        y: plot.grid_row,
        width: 1,
        height: 1,
      })
    ) {
      plotIds.push(plot.plot_id);
    }
  }

  const objectIds: string[] = [];
  for (const object of context.objects) {
    if (
      object.public_id !== context.ignoreObjectId
      && overlaps(geometry, object.geometry)
    ) {
      objectIds.push(object.public_id);
    }
  }

  const house = overlaps(geometry, {
    x: context.housePosition.col,
    y: context.housePosition.row,
    width: context.houseSize.width,
    height: context.houseSize.height,
  });

  return { outOfBounds, plotIds, objectIds, house };
}

interface RenderMapObjectsPanelParams {
  container: HTMLElement | null;
  objects: MapObject[];
  containers: ContainerSummary[];
  plots: Plot[];
  selectedObjectId: string | null;
  showObjects: boolean;
  canWrite: boolean;
  selectedPlotCount: number;
  onToggleObjects: (show: boolean) => void;
  onCreateArea: (type: MapObjectType, name: string) => Promise<boolean>;
  onCreateContainer: (input: {
    name: string;
    container_type: ContainerType;
    parent_object_public_id?: string | null;
  }) => void;
  onUpdateContainer: (plotId: string, patch: ContainerPatch) => void;
  onSelectObject: (publicId: string | null) => void;
  onUpdateObject: (publicId: string, patch: Partial<MapObjectInput>) => void;
  onDeleteObject: (publicId: string) => void;
  onDeleteContainer: (plotId: string) => void;
  onOpenContainer: (plotId: string, trigger: HTMLElement) => void;
}

function areaTypeLabel(type: MapObjectType): string {
  switch (type) {
    case "patio": return t("map.object_patio");
    case "terrace": return t("map.object_terrace");
    case "greenhouse": return t("map.object_greenhouse");
    case "balcony": return t("map.object_balcony");
    default: return t("map.object_other");
  }
}

function containerTypeLabel(type: ContainerType): string {
  switch (type) {
    case "pot": return t("map.container_pot");
    case "planter": return t("map.container_planter");
    case "raised_bed": return t("map.container_raised_bed");
    default: return t("map.container_other");
  }
}

function environmentLabel(environment: ContainerSummary["environment"]): string {
  switch (environment) {
    case "covered": return t("map.environment_covered");
    case "indoor": return t("map.environment_indoor");
    default: return t("map.environment_outdoor");
  }
}

function makeButton(
  className: string,
  label: string,
  title = label,
): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.title = title;
  return button;
}

function makeTextInput(
  value: string,
  placeholder: string,
  disabled: boolean,
): HTMLInputElement {
  const input = document.createElement("input");
  input.type = "text";
  input.value = value;
  input.placeholder = placeholder;
  input.maxLength = 120;
  input.disabled = disabled;
  return input;
}

function makeSelect<T extends string>(
  value: T,
  values: readonly T[],
  labelFor: (value: T) => string,
  disabled: boolean,
): HTMLSelectElement {
  const select = document.createElement("select");
  select.disabled = disabled;
  for (const optionValue of values) {
    const option = document.createElement("option");
    option.value = optionValue;
    option.textContent = labelFor(optionValue);
    select.appendChild(option);
  }
  select.value = value;
  return select;
}

function makeField(label: string, control: HTMLElement): HTMLLabelElement {
  const field = document.createElement("label");
  field.className = "map-object-field";
  const labelText = document.createElement("span");
  labelText.textContent = label;
  field.append(labelText, control);
  return field;
}

function makeNumberInput(value: number, min: number, max: number): HTMLInputElement {
  const input = document.createElement("input");
  input.type = "number";
  input.value = String(value);
  input.min = String(min);
  input.max = String(max);
  input.step = "1";
  input.inputMode = "numeric";
  return input;
}

function positiveInteger(value: string, fallback: number): number {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? Math.max(1, parsed) : fallback;
}

function shapeLabel(shape: MapObjectShape): string {
  return shape === "ellipse" ? t("map.object_ellipse") : t("map.object_rectangle");
}

function plotContainerSummary(plot: Plot): ContainerSummary {
  return {
    plot_id: plot.plot_id,
    display_name: plot.display_name?.trim() || t("map.unnamed_container"),
    container_type: plot.container_type ?? "other",
    environment: plot.environment ?? "outdoor",
    plant_count: plot.plant_count ?? 0,
    parent_map_object_public_id: plot.parent_map_object_public_id ?? null,
    ...(plot.archived_at_ms != null ? { archived_at_ms: plot.archived_at_ms } : {}),
  };
}

function allContainers(params: RenderMapObjectsPanelParams): ContainerSummary[] {
  const byId = new Map<string, ContainerSummary>();
  for (const plot of params.plots) {
    if (plot.plot_kind !== "container" || plot.archived_at_ms != null) continue;
    const container = plotContainerSummary(plot);
    byId.set(container.plot_id, container);
  }
  for (const object of params.objects) {
    for (const container of object.containers ?? []) {
      if (container.archived_at_ms == null) byId.set(container.plot_id, container);
      else byId.delete(container.plot_id);
    }
  }
  for (const container of params.containers) {
    if (container.archived_at_ms == null) byId.set(container.plot_id, container);
    else byId.delete(container.plot_id);
  }
  return [...byId.values()].sort((a, b) =>
    a.display_name.localeCompare(b.display_name, undefined, { sensitivity: "base" }));
}

function areaObjectType(type: MapObjectType): MapObjectType {
  return AREA_TYPES.includes(type as (typeof AREA_TYPES)[number]) ? type : "other";
}

function areaDisplayName(object: MapObject): string {
  return object.name?.trim() || areaTypeLabel(areaObjectType(object.object_type));
}

function buildAreaCreateForm(params: RenderMapObjectsPanelParams): HTMLElement {
  const details = document.createElement("details");
  details.className = "map-object-disclosure";

  const summary = document.createElement("summary");
  summary.className = "map-object-add-summary";
  summary.textContent = `+ ${t("map.area_add")}`;
  details.appendChild(summary);

  const form = document.createElement("form");
  form.className = "map-object-intent-form";
  const typeSelect = makeSelect("patio", AREA_TYPES, areaTypeLabel, !params.canWrite);
  const nameInput = makeTextInput("", t("map.area_name_placeholder"), !params.canWrite);
  const fields = document.createElement("div");
  fields.className = "map-object-form-grid";
  fields.append(
    makeField(t("map.area_type"), typeSelect),
    makeField(t("map.area_name"), nameInput),
  );
  const submit = makeButton("cat-filter-btn map-object-submit-btn", t("map.area_create"));
  submit.type = "submit";
  submit.disabled = !params.canWrite;
  form.append(fields, submit);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!params.canWrite) return;
    void params.onCreateArea(
      typeSelect.value as MapObjectType,
      nameInput.value.trim() || areaTypeLabel(typeSelect.value as MapObjectType),
    ).then((created) => {
      if (!created) {
        details.open = true;
        return;
      }
      details.open = false;
      summary.focus();
    });
  });
  details.appendChild(form);
  return details;
}

function buildContainerCreateForm(
  params: RenderMapObjectsPanelParams,
  parentObjectPublicId: string | null,
  existing?: ContainerSummary,
): HTMLElement {
  const details = document.createElement("details");
  details.className = "map-object-disclosure";
  const summary = document.createElement("summary");
  summary.className = "map-object-add-summary";
  summary.textContent = existing
    ? t("map.container_edit")
    : parentObjectPublicId
      ? `+ ${t("map.container_add")}`
      : `+ ${t("map.container_add_standalone")}`;
  details.appendChild(summary);

  const form = document.createElement("form");
  form.className = "map-object-intent-form";
  const typeSelect = makeSelect(
    existing?.container_type ?? "pot",
    CONTAINER_TYPES,
    containerTypeLabel,
    !params.canWrite,
  );
  const nameInput = makeTextInput(
    existing?.display_name ?? "",
    t("map.container_name_placeholder"),
    !params.canWrite,
  );
  const fields = document.createElement("div");
  fields.className = "map-object-form-grid";
  fields.append(
    makeField(t("map.container_type"), typeSelect),
    makeField(t("map.container_name"), nameInput),
  );
  const submit = makeButton(
    "cat-filter-btn map-object-submit-btn",
    existing ? t("map.container_save") : t("map.container_create"),
  );
  submit.type = "submit";
  submit.disabled = !params.canWrite || (existing?.can_edit === false);
  form.append(fields, submit);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!params.canWrite) return;
    const name = nameInput.value.trim();
    if (!name) {
      nameInput.focus();
      return;
    }
    if (existing) {
      params.onUpdateContainer(existing.plot_id, {
        name,
        container_type: typeSelect.value as ContainerType,
      });
    } else {
      params.onCreateContainer({
        name,
        container_type: typeSelect.value as ContainerType,
        parent_object_public_id: parentObjectPublicId,
      });
    }
    details.open = false;
    summary.focus();
  });
  details.appendChild(form);
  return details;
}

function buildAreaList(params: RenderMapObjectsPanelParams): HTMLElement {
  const list = document.createElement("div");
  list.className = "map-object-list";
  if (params.objects.length === 0) {
    const empty = document.createElement("p");
    empty.className = "map-object-empty";
    empty.textContent = t("map.area_empty");
    list.appendChild(empty);
    return list;
  }

  const containers = allContainers(params);
  for (const object of params.objects) {
    const objectName = areaDisplayName(object);
    const row = document.createElement("div");
    row.className = "map-object-row";
    row.dataset["objectId"] = object.public_id;
    const select = makeButton("map-object-row-main", objectName);
    select.classList.toggle("active", object.public_id === params.selectedObjectId);
    select.setAttribute("aria-pressed", String(object.public_id === params.selectedObjectId));
    select.addEventListener("click", () => {
      params.onSelectObject(
        object.public_id === params.selectedObjectId ? null : object.public_id,
      );
    });
    const meta = document.createElement("span");
    meta.className = "map-object-row-meta";
    meta.textContent = t("map.area_summary", {
      type: areaTypeLabel(areaObjectType(object.object_type)),
      containers: object.container_count ?? containers.filter(
        (container) => container.parent_map_object_public_id === object.public_id,
      ).length,
      plants: object.plant_count ?? 0,
    });
    select.appendChild(meta);
    row.appendChild(select);
    if (params.canWrite) {
      const del = makeButton("map-object-icon-btn", "×", t("common.delete"));
      del.setAttribute("aria-label", `${t("common.delete")} ${objectName}`);
      del.addEventListener("click", () => params.onDeleteObject(object.public_id));
      row.appendChild(del);
    }
    list.appendChild(row);
  }
  return list;
}

function buildContainerRow(
  container: ContainerSummary,
  params: RenderMapObjectsPanelParams,
): HTMLElement {
  const row = document.createElement("div");
  row.className = "map-container-row";
  const open = makeButton("map-container-row-main", container.display_name);
  open.dataset["containerPlotId"] = container.plot_id;
  open.setAttribute("aria-label", `${container.display_name}, ${containerTypeLabel(container.container_type)}`);
  open.addEventListener("click", () => params.onOpenContainer(container.plot_id, open));
  const meta = document.createElement("span");
  meta.className = "map-object-row-meta";
  meta.textContent = `${containerTypeLabel(container.container_type)} · ${t("map.plant_count", { count: container.plant_count })}`;
  if (container.environment !== "outdoor") {
    meta.textContent += ` · ${environmentLabel(container.environment)}`;
  }
  open.appendChild(meta);
  row.appendChild(open);

  if (params.canWrite && container.can_edit !== false) {
    row.appendChild(buildContainerCreateForm(params, container.parent_map_object_public_id ?? null, container));
  }
  if (params.canWrite && container.can_archive === true) {
    const archive = makeButton("map-object-icon-btn", "×", t("map.container_archive"));
    archive.setAttribute("aria-label", `${t("map.container_archive")} ${container.display_name}`);
    archive.addEventListener("click", () => params.onDeleteContainer(container.plot_id));
    row.appendChild(archive);
  }
  return row;
}

function buildSelectedArea(
  params: RenderMapObjectsPanelParams,
  containers: ContainerSummary[],
): HTMLElement | null {
  const selected = params.objects.find((object) => object.public_id === params.selectedObjectId);
  if (!selected) return null;
  const panel = document.createElement("section");
  panel.className = "map-object-detail";
  const heading = document.createElement("div");
  heading.className = "map-object-detail-heading";
  const name = document.createElement("strong");
  name.textContent = areaDisplayName(selected);
  const status = document.createElement("span");
  status.textContent = t("map.area_selected", {
    type: areaTypeLabel(areaObjectType(selected.object_type)),
  });
  heading.append(name, status);

  const actions = document.createElement("div");
  actions.className = "map-object-action-row";
  actions.appendChild(buildContainerCreateForm(params, selected.public_id));
  panel.append(heading, actions);

  const childContainers = containers.filter(
    (container) => container.parent_map_object_public_id === selected.public_id,
  );
  const containerHeading = document.createElement("h4");
  containerHeading.className = "map-object-subheading";
  containerHeading.textContent = t("map.containers_here");
  panel.appendChild(containerHeading);
  const list = document.createElement("div");
  list.className = "map-container-list";
  if (childContainers.length === 0) {
    const empty = document.createElement("p");
    empty.className = "map-object-empty";
    empty.textContent = t("map.containers_empty");
    list.appendChild(empty);
  } else {
    childContainers.forEach((container) => list.appendChild(buildContainerRow(container, params)));
  }
  panel.appendChild(list);

  const layout = document.createElement("details");
  layout.className = "map-object-disclosure map-object-layout-disclosure";
  const layoutSummary = document.createElement("summary");
  layoutSummary.className = "map-object-add-summary";
  layoutSummary.textContent = t("map.edit_layout");
  layout.append(layoutSummary, buildGeometryForm(selected, params));
  panel.appendChild(layout);
  return panel;
}

function buildGeometryForm(
  object: MapObject,
  params: RenderMapObjectsPanelParams,
): HTMLFormElement {
  const form = document.createElement("form");
  form.className = "map-object-geometry-form";
  const nameInput = makeTextInput(areaDisplayName(object), "", !params.canWrite);
  const shapeSelect = makeSelect(object.shape_type, ["rectangle", "ellipse"] as const, shapeLabel, !params.canWrite);
  const colorInput = document.createElement("input");
  colorInput.type = "color";
  colorInput.value = /^#[0-9a-f]{6}$/i.test(object.style.color) ? object.style.color : DEFAULT_AREA_COLOR;
  colorInput.disabled = !params.canWrite;
  const identity = document.createElement("div");
  identity.className = "map-object-form-grid map-object-identity-grid";
  identity.append(
    makeField(t("map.area_name"), nameInput),
    makeField(t("map.object_shape"), shapeSelect),
    makeField(t("map.object_color"), colorInput),
  );
  const geometry = document.createElement("div");
  geometry.className = "map-object-form-grid map-object-position-grid";
  const rowInput = makeNumberInput(object.geometry.y, 1, 100);
  const colInput = makeNumberInput(object.geometry.x, 1, 100);
  const widthInput = makeNumberInput(object.geometry.width, 1, 100);
  const heightInput = makeNumberInput(object.geometry.height, 1, 100);
  [rowInput, colInput, widthInput, heightInput].forEach((input) => {
    input.disabled = !params.canWrite;
  });
  geometry.append(
    makeField(t("map.object_row"), rowInput),
    makeField(t("map.object_col"), colInput),
    makeField(t("map.object_width"), widthInput),
    makeField(t("map.object_height"), heightInput),
  );
  const save = makeButton("cat-filter-btn map-object-submit-btn", t("map.object_save"));
  save.type = "submit";
  save.disabled = !params.canWrite;
  form.append(identity, geometry, save);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!params.canWrite) return;
    const geometryValue: MapObjectGeometry = {
      x: positiveInteger(colInput.value, object.geometry.x),
      y: positiveInteger(rowInput.value, object.geometry.y),
      width: positiveInteger(widthInput.value, object.geometry.width),
      height: positiveInteger(heightInput.value, object.geometry.height),
    };
    params.onUpdateObject(object.public_id, {
      name: nameInput.value.trim() || areaDisplayName(object),
      shape_type: shapeSelect.value as MapObjectShape,
      geometry: geometryValue,
      style: { color: colorInput.value },
    });
  });
  return form;
}

export function renderMapObjectsPanel(params: RenderMapObjectsPanelParams): void {
  if (!params.container) return;
  const containers = allContainers(params);
  const header = document.createElement("div");
  header.className = "map-layer-section-header";
  const title = document.createElement("h3");
  title.textContent = t("map.areas_containers");
  const toggle = makeButton(
    "map-object-toggle",
    params.showObjects ? t("map.object_hide") : t("map.object_show"),
  );
  toggle.classList.toggle("active", params.showObjects);
  toggle.setAttribute("aria-pressed", String(params.showObjects));
  toggle.addEventListener("click", () => params.onToggleObjects(!params.showObjects));
  header.append(title, toggle);

  const standalone = containers.filter((container) => !container.parent_map_object_public_id);
  const standaloneSection = document.createElement("section");
  standaloneSection.className = "map-object-standalone";
  const standaloneHeading = document.createElement("h4");
  standaloneHeading.className = "map-object-subheading";
  standaloneHeading.textContent = t("map.standalone_containers");
  standaloneSection.append(standaloneHeading, buildContainerCreateForm(params, null));
  const standaloneList = document.createElement("div");
  standaloneList.className = "map-container-list";
  if (standalone.length === 0) {
    const empty = document.createElement("p");
    empty.className = "map-object-empty";
    empty.textContent = t("map.standalone_empty");
    standaloneList.appendChild(empty);
  } else {
    standalone.forEach((container) => standaloneList.appendChild(buildContainerRow(container, params)));
  }
  standaloneSection.appendChild(standaloneList);

  params.container.replaceChildren(
    header,
    buildAreaCreateForm(params),
    buildAreaList(params),
    standaloneSection,
    ...(params.selectedObjectId ? [buildSelectedArea(params, containers)].filter(
      (item): item is HTMLElement => item !== null,
    ) : []),
  );
}
