import type {
  MapObject,
  MapObjectGeometry,
  MapObjectInput,
  MapObjectInternalLayout,
  MapObjectShape,
  MapObjectType,
  MapObjectUnitType,
} from "../core/models";
import { t } from "../core/i18n";

const DEFAULT_CUSTOM_COLOR = "#8f9f7d";

export interface MapObjectCustomDraft {
  object_type: MapObjectType;
  name: string;
  shape_type: MapObjectShape;
  style: { color: string };
  has_internal_layout: boolean;
  internal_layout: MapObjectInternalLayout | null;
}

interface RenderMapObjectsPanelParams {
  container: HTMLElement | null;
  objects: MapObject[];
  selectedObjectId: string | null;
  showObjects: boolean;
  canWrite: boolean;
  selectedPlotCount: number;
  onToggleObjects: (show: boolean) => void;
  onCreateObject: (type: MapObjectType) => void;
  onCreateCustomObject: (draft: MapObjectCustomDraft) => void;
  onUpdateObject: (publicId: string, patch: Partial<MapObjectInput>) => void;
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

function shapeTypeLabel(type: MapObjectShape): string {
  return type === "ellipse" ? t("map.object_ellipse") : t("map.object_rectangle");
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

function makeField(label: string, control: HTMLElement): HTMLLabelElement {
  const field = document.createElement("label");
  field.className = "map-object-field";
  const text = document.createElement("span");
  text.textContent = label;
  field.append(text, control);
  return field;
}

function makeTextInput(value: string, disabled: boolean): HTMLInputElement {
  const input = document.createElement("input");
  input.type = "text";
  input.value = value;
  input.maxLength = 120;
  input.disabled = disabled;
  return input;
}

function makeNumberInput(value: number, min: number, max: number, disabled: boolean): HTMLInputElement {
  const input = document.createElement("input");
  input.type = "number";
  input.value = String(value);
  input.min = String(min);
  input.max = String(max);
  input.step = "1";
  input.inputMode = "numeric";
  input.disabled = disabled;
  return input;
}

function makeColorInput(value: string, disabled: boolean): HTMLInputElement {
  const input = document.createElement("input");
  input.type = "color";
  input.value = /^#[0-9a-f]{6}$/i.test(value) ? value : DEFAULT_CUSTOM_COLOR;
  input.disabled = disabled;
  return input;
}

function makeShapeSelect(value: MapObjectShape, disabled: boolean): HTMLSelectElement {
  const select = document.createElement("select");
  select.disabled = disabled;
  for (const shape of ["rectangle", "ellipse"] as const) {
    const option = document.createElement("option");
    option.value = shape;
    option.textContent = shapeTypeLabel(shape);
    select.appendChild(option);
  }
  select.value = value;
  return select;
}

function makeObjectTypeSelect(value: MapObjectType, disabled: boolean): HTMLSelectElement {
  const select = document.createElement("select");
  select.disabled = disabled;
  select.className = "map-object-type-select";
  for (const type of [
    "patio", "terrace", "greenhouse", "shed", "pond", "path", "bed", "other",
  ] as const) {
    const option = document.createElement("option");
    option.value = type;
    option.textContent = objectTypeLabel(type);
    select.appendChild(option);
  }
  select.value = value;
  return select;
}

function positiveIntegerValue(input: HTMLInputElement, fallback: number): number {
  const value = Number.parseInt(input.value, 10);
  if (!Number.isFinite(value)) return fallback;
  return Math.max(1, value);
}

function shapeValue(select: HTMLSelectElement): MapObjectShape {
  return select.value === "ellipse" ? "ellipse" : "rectangle";
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

function buildCustomObjectForm(params: RenderMapObjectsPanelParams): HTMLFormElement {
  const form = document.createElement("form");
  form.className = "map-object-custom-form";

  const title = document.createElement("strong");
  title.className = "map-object-form-title";
  title.textContent = t("map.object_custom");

  const fields = document.createElement("div");
  fields.className = "map-object-form-grid map-object-identity-grid";

  const nameInput = makeTextInput(t("map.object_custom"), !params.canWrite);
  const typeSelect = makeObjectTypeSelect("other", !params.canWrite);
  const shapeSelect = makeShapeSelect("rectangle", !params.canWrite);
  const colorInput = makeColorInput(DEFAULT_CUSTOM_COLOR, !params.canWrite);
  const layoutToggle = document.createElement("input");
  layoutToggle.type = "checkbox";
  layoutToggle.disabled = !params.canWrite;

  const layoutLabel = document.createElement("label");
  layoutLabel.className = "map-object-checkbox-field";
  const layoutText = document.createElement("span");
  layoutText.textContent = t("map.object_layout");
  layoutLabel.append(layoutToggle, layoutText);

  fields.append(
    makeField(t("map.object_name"), nameInput),
    makeField(t("map.object_type"), typeSelect),
    makeField(t("map.object_shape"), shapeSelect),
    makeField(t("map.object_color"), colorInput),
    layoutLabel,
  );

  const layoutFields = document.createElement("div");
  layoutFields.className = "map-object-form-grid map-object-layout-grid";
  const layoutRows = makeNumberInput(6, 1, 100, !params.canWrite);
  const layoutCols = makeNumberInput(8, 1, 100, !params.canWrite);
  layoutFields.hidden = true;
  layoutFields.append(
    makeField(t("map.object_layout_rows"), layoutRows),
    makeField(t("map.object_layout_cols"), layoutCols),
  );

  layoutToggle.addEventListener("change", () => {
    layoutFields.hidden = !layoutToggle.checked;
  });

  const submit = makeButton("cat-filter-btn map-object-submit-btn", `+ ${t("map.object_create_custom")}`);
  submit.type = "submit";
  submit.disabled = !params.canWrite;

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!params.canWrite) return;
    const name = nameInput.value.trim() || t("map.object_custom");
    params.onCreateCustomObject({
      object_type: typeSelect.value as MapObjectType,
      name,
      shape_type: shapeValue(shapeSelect),
      style: { color: colorInput.value },
      has_internal_layout: layoutToggle.checked,
      internal_layout: layoutToggle.checked
        ? {
            rows: positiveIntegerValue(layoutRows, 6),
            cols: positiveIntegerValue(layoutCols, 8),
          }
        : null,
    });
  });

  form.append(title, fields, layoutFields, submit);
  return form;
}

function buildCreateArea(params: RenderMapObjectsPanelParams): HTMLElement {
  const area = document.createElement("div");
  area.className = "map-object-create-stack";
  area.append(buildCreateRow(params), buildCustomObjectForm(params));
  return area;
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

function buildGeometryForm(
  object: MapObject,
  params: RenderMapObjectsPanelParams,
): HTMLFormElement {
  const form = document.createElement("form");
  form.className = "map-object-geometry-form";

  const nameInput = makeTextInput(object.name, !params.canWrite);
  const shapeSelect = makeShapeSelect(object.shape_type, !params.canWrite);
  const colorInput = makeColorInput(object.style.color, !params.canWrite);
  const rowInput = makeNumberInput(object.geometry.y, 1, 100, !params.canWrite);
  const colInput = makeNumberInput(object.geometry.x, 1, 100, !params.canWrite);
  const widthInput = makeNumberInput(object.geometry.width, 1, 100, !params.canWrite);
  const heightInput = makeNumberInput(object.geometry.height, 1, 100, !params.canWrite);
  const layoutToggle = document.createElement("input");
  layoutToggle.type = "checkbox";
  layoutToggle.checked = object.has_internal_layout;
  layoutToggle.disabled = !params.canWrite || (object.has_internal_layout && object.units.length > 0);

  const identity = document.createElement("div");
  identity.className = "map-object-form-grid map-object-identity-grid";
  identity.append(
    makeField(t("map.object_name"), nameInput),
    makeField(t("map.object_shape"), shapeSelect),
    makeField(t("map.object_color"), colorInput),
  );

  const geometry = document.createElement("div");
  geometry.className = "map-object-form-grid map-object-position-grid";
  geometry.append(
    makeField(t("map.object_row"), rowInput),
    makeField(t("map.object_col"), colInput),
    makeField(t("map.object_width"), widthInput),
    makeField(t("map.object_height"), heightInput),
  );

  const layoutLabel = document.createElement("label");
  layoutLabel.className = "map-object-checkbox-field";
  const layoutText = document.createElement("span");
  layoutText.textContent = t("map.object_layout");
  layoutLabel.append(layoutToggle, layoutText);

  const layoutFields = document.createElement("div");
  layoutFields.className = "map-object-form-grid map-object-layout-grid";
  const rowsInput = makeNumberInput(object.internal_layout.rows, 1, 100, !params.canWrite);
  const colsInput = makeNumberInput(object.internal_layout.cols, 1, 100, !params.canWrite);
  layoutFields.hidden = !layoutToggle.checked;
  layoutFields.append(
    makeField(t("map.object_layout_rows"), rowsInput),
    makeField(t("map.object_layout_cols"), colsInput),
  );

  layoutToggle.addEventListener("change", () => {
    layoutFields.hidden = !layoutToggle.checked;
  });

  const submit = makeButton("cat-filter-btn map-object-submit-btn", t("map.object_save"));
  submit.type = "submit";
  submit.disabled = !params.canWrite;

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!params.canWrite) return;
    const nextGeometry: MapObjectGeometry = {
      x: positiveIntegerValue(colInput, object.geometry.x),
      y: positiveIntegerValue(rowInput, object.geometry.y),
      width: positiveIntegerValue(widthInput, object.geometry.width),
      height: positiveIntegerValue(heightInput, object.geometry.height),
    };
    const patch: Partial<MapObjectInput> = {
      name: nameInput.value.trim() || object.name,
      shape_type: shapeValue(shapeSelect),
      geometry: nextGeometry,
      style: { color: colorInput.value },
      has_internal_layout: layoutToggle.checked,
    };
    if (layoutToggle.checked) {
      patch.internal_layout = {
        rows: positiveIntegerValue(rowsInput, object.internal_layout.rows),
        cols: positiveIntegerValue(colsInput, object.internal_layout.cols),
      };
    }
    params.onUpdateObject(object.public_id, patch);
  });

  form.append(identity, geometry, layoutLabel, layoutFields, submit);
  return form;
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
  status.textContent = selected.has_internal_layout
    ? t("map.object_layout_only")
    : t("map.object_layout_disabled");
  heading.append(name, status);

  panel.append(heading, buildGeometryForm(selected, params));

  if (!selected.has_internal_layout) {
    const empty = document.createElement("p");
    empty.className = "map-object-layout-empty";
    empty.textContent = t("map.object_layout_disabled");
    panel.appendChild(empty);
    return panel;
  }

  const actions = document.createElement("div");
  actions.className = "map-object-create-row";
  const pot = makeButton("cat-filter-btn", `+ ${t("map.unit_pot")}`);
  pot.disabled = !params.canWrite;
  pot.addEventListener("click", () => params.onAddUnit(selected.public_id, "pot"));
  const planter = makeButton("cat-filter-btn", `+ ${t("map.unit_planter")}`);
  planter.disabled = !params.canWrite;
  planter.addEventListener("click", () => params.onAddUnit(selected.public_id, "planter"));
  actions.append(pot, planter);

  panel.append(actions, buildUnitGrid(selected, params));
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
    buildCreateArea(params),
    buildObjectList(params),
    ...(selectedPanel ? [selectedPanel] : []),
  );
}
