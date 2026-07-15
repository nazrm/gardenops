import type { ProcurementItem } from "../core/models";
import { t } from "../core/i18n";
import { createFieldGroup as _createFieldGroup } from "../core/dom";
import { canonicalDecimalString, compareDecimalStrings } from "../services/api";

const STATUS_ICONS: Record<string, string> = {
  wanted: "\uD83D\uDCAD",
  ordered: "\uD83D\uDED2",
  shipped: "\uD83D\uDCE6",
  received: "\u2705",
  cancelled: "\u274C",
};

const NEXT_TRANSITION: Record<string, { to: string; labelKey: string } | null> = {
  wanted: { to: "ordered", labelKey: "procurement.transition_order" },
  ordered: { to: "shipped", labelKey: "procurement.transition_ship" },
  shipped: { to: "received", labelKey: "procurement.transition_receive" },
  received: null,
  cancelled: { to: "wanted", labelKey: "procurement.transition_reopen" },
};

export interface ProcurementListCallbacks {
  onEdit: (item: ProcurementItem) => void;
  onTransition: (item: ProcurementItem, toStatus: string) => void | Promise<void>;
  onDelete: (item: ProcurementItem) => void | Promise<void>;
  onPlantClick: (pltId: string) => void;
  onPlotClick: (plotId: string) => void;
  canWrite?: boolean | undefined;
}

export function renderProcurementList(
  container: HTMLElement,
  items: ProcurementItem[],
  cbs: ProcurementListCallbacks,
  plantNames?: Map<string, string>,
): void {
  container.replaceChildren();
  if (items.length === 0) {
    container.appendChild(createProcurementEmptyState());
    return;
  }
  for (const item of items) {
    container.appendChild(createProcurementCard(item, cbs, plantNames));
  }
}

function createProcurementEmptyState(): HTMLElement {
  const empty = document.createElement("div");
  empty.className = "procurement-empty";

  const message = document.createElement("p");
  message.textContent = t("procurement.empty");

  const hint = document.createElement("p");
  hint.textContent = t("procurement.empty_hint");
  hint.style.color = "var(--text-3)";
  hint.style.fontSize = "0.85rem";

  empty.append(message, hint);
  return empty;
}

function createProcurementCard(
  item: ProcurementItem,
  cbs: ProcurementListCallbacks,
  plantNames?: Map<string, string>,
): HTMLElement {
  const card = document.createElement("div");
  card.className = `procurement-card status-${item.status}`;

  // Header: icon + label + status chip
  const header = document.createElement("div");
  header.className = "procurement-card-header";

  const icon = document.createElement("span");
  icon.className = "procurement-card-icon";
  icon.textContent = STATUS_ICONS[item.status] || "\uD83D\uDCAD";

  const label = document.createElement("span");
  label.className = "procurement-card-label";
  label.textContent = item.label;

  const statusChip = document.createElement("span");
  statusChip.className = `procurement-status-chip status-${item.status}`;
  statusChip.textContent = t(`procurement.status_${item.status}`);

  header.append(icon, label, statusChip);
  card.appendChild(header);

  // Vendor
  if (item.vendor_name) {
    const vendor = document.createElement("div");
    vendor.className = "procurement-card-vendor";
    vendor.textContent = item.vendor_name;
    card.appendChild(vendor);
  }

  // Meta: type badge, cost, quantity
  const meta = document.createElement("div");
  meta.className = "procurement-card-meta";

  const typeBadge = document.createElement("span");
  typeBadge.textContent = t(`inventory.type.${item.inventory_type}`);
  meta.appendChild(typeBadge);

  if (item.cost_minor > 0) {
    const cost = document.createElement("span");
    cost.className = "procurement-card-cost";
    cost.textContent = t("procurement.cost_display", {
      amount: item.cost_minor,
      currency: item.currency,
    });
    meta.appendChild(cost);
  }

  const qty = document.createElement("span");
  qty.textContent = `${item.quantity} ${item.unit}`;
  meta.appendChild(qty);

  card.appendChild(meta);

  // Dates
  const datesParts: string[] = [];
  if (item.ordered_on) datesParts.push(`${t("procurement.form_ordered")}: ${item.ordered_on}`);
  if (item.expected_on) datesParts.push(`${t("procurement.form_expected")}: ${item.expected_on}`);
  if (item.received_on) datesParts.push(`${t("procurement.form_ordered").replace("Ordered", "Received")}: ${item.received_on}`);
  if (datesParts.length > 0) {
    const dates = document.createElement("div");
    dates.className = "procurement-card-dates";
    dates.textContent = datesParts.join(" \u00B7 ");
    card.appendChild(dates);
  }

  // Notes
  if (item.notes) {
    const notes = document.createElement("div");
    notes.className = "procurement-card-notes";
    notes.textContent = item.notes;
    card.appendChild(notes);
  }

  // Linked plant/plot tags
  if (item.linked_plt_id || item.linked_plot_id) {
    const tagWrap = document.createElement("div");
    tagWrap.className = "procurement-card-meta";
    if (item.linked_plt_id) {
      const plantTag = document.createElement("button");
      plantTag.type = "button";
      plantTag.className = "journal-tag journal-tag-plant";
      plantTag.textContent = plantNames?.get(item.linked_plt_id) ?? item.linked_plt_id;
      plantTag.addEventListener("click", () => cbs.onPlantClick(item.linked_plt_id!));
      tagWrap.appendChild(plantTag);
    }
    if (item.linked_plot_id) {
      const plotTag = document.createElement("button");
      plotTag.type = "button";
      plotTag.className = "procurement-action-btn";
      plotTag.textContent = item.linked_plot_id;
      plotTag.addEventListener("click", () => cbs.onPlotClick(item.linked_plot_id!));
      tagWrap.appendChild(plotTag);
    }
    card.appendChild(tagWrap);
  }

  const rawInventoryItemId = item.metadata?.["inventory_item_id"];
  const inventoryItemId = typeof rawInventoryItemId === "string"
    ? rawInventoryItemId.trim()
    : typeof rawInventoryItemId === "number"
      ? String(rawInventoryItemId)
      : "";
  if (inventoryItemId) {
    const linkedInventory = document.createElement("div");
    linkedInventory.className = "procurement-card-dates";
    linkedInventory.textContent = t("procurement.inventory_linked", { id: inventoryItemId });
    card.appendChild(linkedInventory);
  }

  // Actions
  if (cbs.canWrite !== false) {
    const actions = document.createElement("div");
    actions.className = "procurement-card-actions";
    let pending = false;
    const runMutation = (action: () => void | Promise<void>) => {
      if (pending) return;
      pending = true;
      card.setAttribute("aria-busy", "true");
      actions.querySelectorAll<HTMLButtonElement>("button").forEach((button) => {
        button.disabled = true;
      });
      void Promise.resolve(action()).finally(() => {
        if (!card.isConnected) return;
        pending = false;
        card.removeAttribute("aria-busy");
        actions.querySelectorAll<HTMLButtonElement>("button").forEach((button) => {
          button.disabled = false;
        });
      });
    };

    const nextTransition = NEXT_TRANSITION[item.status];
    if (nextTransition) {
      const transBtn = document.createElement("button");
      transBtn.type = "button";
      transBtn.className = "procurement-action-btn procurement-action-transition";
      transBtn.textContent = t(nextTransition.labelKey);
      transBtn.addEventListener("click", () => {
        runMutation(() => cbs.onTransition(item, nextTransition.to));
      });
      actions.appendChild(transBtn);
    }

    if (item.status !== "cancelled") {
      const cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.className = "procurement-action-btn procurement-action-delete";
      cancelBtn.textContent = t("procurement.transition_cancel");
      cancelBtn.addEventListener("click", () => {
        runMutation(() => cbs.onTransition(item, "cancelled"));
      });
      actions.appendChild(cancelBtn);
    }

    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "procurement-action-btn";
    editBtn.textContent = t("common.edit");
    editBtn.addEventListener("click", () => cbs.onEdit(item));
    actions.appendChild(editBtn);

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "procurement-action-btn procurement-action-delete";
    deleteBtn.textContent = t("common.delete");
    deleteBtn.addEventListener("click", () => {
      runMutation(() => cbs.onDelete(item));
    });
    actions.appendChild(deleteBtn);

    card.appendChild(actions);
  }
  return card;
}

export interface ProcurementFormOptions {
  item: ProcurementItem | undefined;
  availablePlants?: Array<{ plt_id: string; name: string }>;
  availablePlots?: string[];
  onSave: (data: Record<string, unknown>) => void | Promise<void>;
  onCancel: () => void;
}

export function createProcurementForm(options: ProcurementFormOptions): HTMLElement {
  const { item, onSave, onCancel } = options;
  const form = document.createElement("form");
  form.className = "overlay-form";
  form.addEventListener("submit", (e) => e.preventDefault());

  const title = document.createElement("h3");
  title.textContent = t("procurement.form_title");
  form.appendChild(title);

  // Label
  const labelGroup = createFieldGroup(t("procurement.form_label"), "procurement-label");
  const labelInput = document.createElement("input");
  labelInput.id = "procurement-label";
  labelInput.type = "text";
  labelInput.maxLength = 200;
  labelInput.required = true;
  labelInput.value = item?.label || "";
  labelGroup.appendChild(labelInput);
  form.appendChild(labelGroup);

  // Inventory type
  const typeGroup = createFieldGroup(t("procurement.form_type"), "procurement-type");
  const typeSelect = document.createElement("select");
  typeSelect.id = "procurement-type";
  for (const opt of ["seed", "bulb", "tuber", "division", "bare_root", "nursery", "cutting", "other"]) {
    const option = document.createElement("option");
    option.value = opt;
    option.textContent = t(`inventory.type.${opt}`);
    if (item?.inventory_type === opt) option.selected = true;
    typeSelect.appendChild(option);
  }
  if (!item) typeSelect.value = "other";
  typeGroup.appendChild(typeSelect);
  form.appendChild(typeGroup);

  // Vendor name
  const vendorGroup = createFieldGroup(t("procurement.form_vendor"), "procurement-vendor");
  const vendorInput = document.createElement("input");
  vendorInput.id = "procurement-vendor";
  vendorInput.type = "text";
  vendorInput.maxLength = 200;
  vendorInput.value = item?.vendor_name || "";
  vendorGroup.appendChild(vendorInput);
  form.appendChild(vendorGroup);

  // Vendor URL
  const urlGroup = createFieldGroup(t("procurement.form_vendor_url"), "procurement-vendor-url");
  const urlInput = document.createElement("input");
  urlInput.id = "procurement-vendor-url";
  urlInput.type = "url";
  urlInput.maxLength = 500;
  urlInput.value = item?.vendor_url || "";
  urlGroup.appendChild(urlInput);
  form.appendChild(urlGroup);

  // Cost + currency (inline row)
  const costRow = document.createElement("div");
  costRow.style.display = "flex";
  costRow.style.gap = "var(--sp-2)";

  const costGroup = createFieldGroup(t("procurement.form_cost"), "procurement-cost");
  costGroup.style.flex = "1";
  const costInput = document.createElement("input");
  costInput.id = "procurement-cost";
  costInput.type = "number";
  costInput.min = "0";
  costInput.step = "0.01";
  costInput.value = item ? (item.cost_minor / 100).toFixed(2) : "0.00";
  costGroup.appendChild(costInput);

  const currGroup = createFieldGroup(t("procurement.form_currency"), "procurement-currency");
  currGroup.style.width = "80px";
  const currInput = document.createElement("input");
  currInput.id = "procurement-currency";
  currInput.type = "text";
  currInput.maxLength = 10;
  currInput.value = item?.currency || "NOK";
  currGroup.appendChild(currInput);

  costRow.append(costGroup, currGroup);
  form.appendChild(costRow);

  // Quantity + unit (inline row)
  const qtyRow = document.createElement("div");
  qtyRow.style.display = "flex";
  qtyRow.style.gap = "var(--sp-2)";

  const qtyGroup = createFieldGroup(t("procurement.form_quantity"), "procurement-quantity");
  qtyGroup.style.flex = "1";
  const qtyInput = document.createElement("input");
  qtyInput.id = "procurement-quantity";
  qtyInput.type = "number";
  qtyInput.min = "0.000001";
  qtyInput.step = "0.000001";
  qtyInput.value = item?.quantity ?? "1";
  qtyGroup.appendChild(qtyInput);

  const unitGroup = createFieldGroup(t("procurement.form_unit"), "procurement-unit");
  unitGroup.style.width = "100px";
  const unitInput = document.createElement("input");
  unitInput.id = "procurement-unit";
  unitInput.type = "text";
  unitInput.maxLength = 50;
  unitInput.value = item?.unit || "pieces";
  unitGroup.appendChild(unitInput);

  qtyRow.append(qtyGroup, unitGroup);
  form.appendChild(qtyRow);

  // Ordered on
  const orderedGroup = createFieldGroup(t("procurement.form_ordered"), "procurement-ordered-on");
  const orderedInput = document.createElement("input");
  orderedInput.id = "procurement-ordered-on";
  orderedInput.type = "date";
  orderedInput.value = item?.ordered_on || "";
  orderedGroup.appendChild(orderedInput);
  form.appendChild(orderedGroup);

  // Expected on
  const expectedGroup = createFieldGroup(t("procurement.form_expected"), "procurement-expected-on");
  const expectedInput = document.createElement("input");
  expectedInput.id = "procurement-expected-on";
  expectedInput.type = "date";
  expectedInput.value = item?.expected_on || "";
  expectedGroup.appendChild(expectedInput);
  form.appendChild(expectedGroup);

  // Linked plant ID
  const plantGroup = createFieldGroup(t("procurement.form_plant"), "procurement-plant");
  const plantInput = document.createElement("input");
  plantInput.id = "procurement-plant";
  plantInput.type = "text";
  plantInput.value = item?.linked_plt_id || "";
  if (options.availablePlants && options.availablePlants.length > 0) {
    const plantDatalist = document.createElement("datalist");
    plantDatalist.id = "procurement-plant-suggestions";
    for (const plant of options.availablePlants) {
      const opt = document.createElement("option");
      opt.value = plant.plt_id;
      opt.label = `${plant.plt_id} — ${plant.name}`;
      plantDatalist.appendChild(opt);
    }
    plantGroup.appendChild(plantDatalist);
    plantInput.setAttribute("list", "procurement-plant-suggestions");
  }
  plantGroup.appendChild(plantInput);
  form.appendChild(plantGroup);

  // Linked plot ID
  const plotGroup = createFieldGroup(t("procurement.form_plot"), "procurement-plot");
  const plotInput = document.createElement("input");
  plotInput.id = "procurement-plot";
  plotInput.type = "text";
  plotInput.value = item?.linked_plot_id || "";
  if (options.availablePlots && options.availablePlots.length > 0) {
    const plotDatalist = document.createElement("datalist");
    plotDatalist.id = "procurement-plot-suggestions";
    for (const plotId of options.availablePlots) {
      const opt = document.createElement("option");
      opt.value = plotId;
      plotDatalist.appendChild(opt);
    }
    plotGroup.appendChild(plotDatalist);
    plotInput.setAttribute("list", "procurement-plot-suggestions");
  }
  plotGroup.appendChild(plotInput);
  form.appendChild(plotGroup);

  // Notes
  const notesGroup = createFieldGroup(t("procurement.form_notes"), "procurement-notes");
  const notesInput = document.createElement("textarea");
  notesInput.id = "procurement-notes";
  notesInput.maxLength = 2000;
  notesInput.rows = 3;
  notesInput.value = item?.notes || "";
  notesGroup.appendChild(notesInput);
  form.appendChild(notesGroup);

  // Buttons
  const btnRow = document.createElement("div");
  btnRow.className = "overlay-btn-row";

  const saveBtn = document.createElement("button");
  saveBtn.id = "procurement-save-btn";
  saveBtn.type = "button";
  saveBtn.className = "btn btn-primary";
  saveBtn.textContent = t("common.save");
  let pending = false;
  saveBtn.addEventListener("click", () => {
    if (pending) return;
    if (!labelInput.value.trim()) return;
    let quantity: string;
    try {
      quantity = canonicalDecimalString(qtyInput.value);
    } catch {
      return;
    }
    if (compareDecimalStrings(quantity, "0") <= 0) return;
    const costValue = Math.round(parseFloat(costInput.value || "0") * 100);
    const data: Record<string, unknown> = {
      label: labelInput.value.trim(),
      inventory_type: typeSelect.value,
      vendor_name: vendorInput.value.trim(),
      vendor_url: urlInput.value.trim(),
      cost_minor: costValue,
      currency: currInput.value.trim() || "NOK",
      quantity,
      unit: unitInput.value.trim() || "pieces",
      notes: notesInput.value.trim(),
    };
    if (orderedInput.value) data["ordered_on"] = orderedInput.value;
    if (expectedInput.value) data["expected_on"] = expectedInput.value;
    const pltId = plantInput.value.trim();
    if (pltId) data["linked_plt_id"] = pltId;
    else data["linked_plt_id"] = null;
    const plotId = plotInput.value.trim();
    if (plotId) data["linked_plot_id"] = plotId;
    else data["linked_plot_id"] = null;
    pending = true;
    saveBtn.disabled = true;
    cancelBtn.disabled = true;
    form.setAttribute("aria-busy", "true");
    void Promise.resolve(onSave(data)).finally(() => {
      if (!form.isConnected) return;
      pending = false;
      saveBtn.disabled = false;
      cancelBtn.disabled = false;
      form.removeAttribute("aria-busy");
    });
  });

  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "btn";
  cancelBtn.textContent = t("common.cancel");
  cancelBtn.addEventListener("click", onCancel);

  btnRow.append(saveBtn, cancelBtn);
  form.appendChild(btnRow);

  return form;
}

function createFieldGroup(label: string, controlId: string): HTMLElement {
  const group = _createFieldGroup(label, "overlay-field-group");
  group.querySelector("label")?.setAttribute("for", controlId);
  return group;
}
