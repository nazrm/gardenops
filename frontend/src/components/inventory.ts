import type {
  InventoryItem,
  InventoryProcurementHistoryEntry,
  InventoryTransaction,
  InventoryType,
  TransactionReason,
} from "../services/api";
import { getLocaleTag, t } from "../core/i18n";
import { renderEmptyState } from "./emptyState";

// ── Labels ────────────────────────────────────────────────

const INVENTORY_TYPE_TRANSLATION_KEYS: Record<InventoryType, string> = {
  seed: "inventory.type.seed",
  bulb: "inventory.type.bulb",
  tuber: "inventory.type.tuber",
  division: "inventory.type.division",
  bare_root: "inventory.type.bare_root",
  nursery: "inventory.type.nursery",
  cutting: "inventory.type.cutting",
  other: "inventory.type.other",
};

const TRANSACTION_REASON_TRANSLATION_KEYS: Record<string, string> = {
  purchased: "inventory.reason.purchased",
  harvested: "inventory.reason.harvested",
  sowed: "inventory.reason.sowed",
  planted: "inventory.reason.planted",
  divided: "inventory.reason.divided",
  gifted: "inventory.reason.gifted",
  disposed: "inventory.reason.disposed",
  adjusted: "inventory.reason.adjusted",
  "": "inventory.reason.other",
};

const TYPE_ICONS: Record<InventoryType, string> = {
  seed: "\u{1F331}",
  bulb: "\u{1F9C5}",
  tuber: "\u{1F954}",
  division: "\u{2702}\uFE0F",
  bare_root: "\u{1FAB5}",
  nursery: "\u{1FAB4}",
  cutting: "\u{1F33F}",
  other: "\u{1F4E6}",
};

function formatDate(isoDate: string): string {
  try {
    const d = new Date(isoDate + "T00:00:00");
    return d.toLocaleDateString(getLocaleTag(), {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return isoDate;
  }
}

function inventoryTypeLabel(type: InventoryType): string {
  return t(INVENTORY_TYPE_TRANSLATION_KEYS[type] ?? type);
}

function transactionReasonLabel(reason: string): string {
  return t(TRANSACTION_REASON_TRANSLATION_KEYS[reason] ?? "inventory.reason.other");
}

// ── Callbacks ─────────────────────────────────────────────

export interface InventoryListCallbacks {
  onAddStock: (item: InventoryItem) => void;
  onConsumeStock: (item: InventoryItem) => void;
  onPlantFromStock: (item: InventoryItem) => void;
  onEdit: (item: InventoryItem) => void;
  onDelete: (item: InventoryItem) => void;
  onViewTransactions: (item: InventoryItem) => void;
  onPlantClick: (pltId: string) => void;
  canWrite?: boolean | undefined;
}

// ── Inventory list ────────────────────────────────────────

export function renderInventoryList(
  container: HTMLElement,
  items: InventoryItem[],
  cbs: InventoryListCallbacks,
  plantNames?: Map<string, string>,
): void {
  container.replaceChildren();
  if (items.length === 0) {
    renderEmptyState(container, {
      icon: "\uD83D\uDCE6",
      headline: t("inventory.empty"),
      hint: t("inventory.empty_hint"),
    });
    return;
  }
  for (const item of items) {
    container.appendChild(createInventoryCard(item, cbs, plantNames));
  }
}

function createInventoryCard(
  item: InventoryItem,
  cbs: InventoryListCallbacks,
  plantNames?: Map<string, string>,
): HTMLElement {
  const card = document.createElement("div");
  card.className = "inventory-card";
  card.dataset["itemId"] = String(item.id);

  // Header
  const header = document.createElement("div");
  header.className = "inventory-card-header";

  const icon = document.createElement("span");
  icon.className = "inventory-card-icon";
  icon.textContent = TYPE_ICONS[item.inventory_type] ?? "\u{1F4E6}";

  const title = document.createElement("span");
  title.className = "inventory-card-title";
  title.textContent = item.label || t("inventory.untitled");

  const qtyBadge = document.createElement("span");
  qtyBadge.className = `inventory-qty-badge${item.quantity <= 0 ? " inventory-qty-zero" : ""}`;
  qtyBadge.textContent = `${item.quantity} ${item.unit}`;

  header.append(icon, title, qtyBadge);
  card.appendChild(header);

  // Meta row
  const meta = document.createElement("div");
  meta.className = "inventory-card-meta";

  const typeBadge = document.createElement("span");
  typeBadge.className = "inventory-type-badge";
  typeBadge.textContent = inventoryTypeLabel(item.inventory_type);
  meta.appendChild(typeBadge);

  if (item.plt_id) {
    const plantBtn = document.createElement("button");
    plantBtn.type = "button";
    plantBtn.className = "journal-tag journal-tag-plant";
    plantBtn.textContent = plantNames?.get(item.plt_id) ?? item.plt_id;
    plantBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      cbs.onPlantClick(item.plt_id!);
    });
    meta.appendChild(plantBtn);
  }
  card.appendChild(meta);

  const procurementSummary = createProcurementSummary(item.procurement_history);
  if (procurementSummary) {
    const sourcing = document.createElement("div");
    sourcing.className = "inventory-card-meta";
    sourcing.append(
      createSourceSummaryLabel(),
      document.createTextNode(procurementSummary),
    );
    card.appendChild(sourcing);
  }

  // Actions
  const actions = document.createElement("div");
  actions.className = "inventory-card-actions";

  const histBtn = createActionButton(t("inventory.action_history"), "inventory-action-history", () => cbs.onViewTransactions(item));
  actions.appendChild(histBtn);
  if (cbs.canWrite !== false) {
    const addBtn = createActionButton(t("inventory.action_add_stock"), "inventory-action-add", () => cbs.onAddStock(item));
    const useBtn = createActionButton(t("inventory.action_use_stock"), "inventory-action-use", () => cbs.onConsumeStock(item));
    const plantBtn = createActionButton(t("inventory.action_plant"), "inventory-action-plant", () => cbs.onPlantFromStock(item));
    const editBtn = createActionButton(t("common.edit"), "inventory-action-edit", () => cbs.onEdit(item));
    const delBtn = createActionButton(t("common.delete"), "inventory-action-delete journal-action-delete", () => cbs.onDelete(item));
    actions.prepend(addBtn, useBtn, plantBtn);
    actions.append(editBtn, delBtn);
  }
  card.appendChild(actions);

  return card;
}

function createActionButton(label: string, className: string, onClick: () => void): HTMLButtonElement {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = `inventory-action-btn ${className}`;
  btn.textContent = label;
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    onClick();
  });
  return btn;
}

// ── Inventory table (desktop) ─────────────────────────────

export function renderInventoryTable(
  thead: HTMLElement,
  tbody: HTMLElement,
  items: InventoryItem[],
  cbs: InventoryListCallbacks,
  plantNames?: Map<string, string>,
): void {
  thead.replaceChildren();
  tbody.replaceChildren();

  const headerRow = document.createElement("tr");
  for (const col of [
    t("inventory.column_label"),
    t("inventory.column_plant"),
    t("inventory.column_type"),
    t("inventory.column_qty"),
    t("common.actions"),
  ]) {
    const th = document.createElement("th");
    th.textContent = col;
    if (col === t("common.actions")) th.style.width = "220px";
    headerRow.appendChild(th);
  }
  thead.appendChild(headerRow);

  if (items.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5;
    td.className = "adm-empty";
    td.textContent = t("inventory.empty_short");
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  for (const item of items) {
    const tr = document.createElement("tr");
    tr.dataset["itemId"] = String(item.id);

    // Label
    const tdLabel = document.createElement("td");
    const labelIcon = document.createElement("span");
    labelIcon.textContent = (TYPE_ICONS[item.inventory_type] ?? "") + " ";
    const labelText = document.createElement("span");
    labelText.textContent = item.label || t("inventory.untitled");
    tdLabel.append(labelIcon, labelText);
    tr.appendChild(tdLabel);

    // Plant
    const tdPlant = document.createElement("td");
    if (item.plt_id) {
      const plantBtn = document.createElement("button");
      plantBtn.type = "button";
      plantBtn.className = "journal-tag journal-tag-plant";
      plantBtn.textContent = plantNames?.get(item.plt_id) ?? item.plt_id;
      plantBtn.addEventListener("click", () => cbs.onPlantClick(item.plt_id!));
      tdPlant.appendChild(plantBtn);
    } else {
      tdPlant.textContent = "\u2014";
    }
    tr.appendChild(tdPlant);

    // Type
    const tdType = document.createElement("td");
    const typeBadge = document.createElement("span");
    typeBadge.className = "inventory-type-badge";
    typeBadge.textContent = inventoryTypeLabel(item.inventory_type);
    tdType.appendChild(typeBadge);
    tr.appendChild(tdType);

    // Qty
    const tdQty = document.createElement("td");
    tdQty.className = item.quantity <= 0 ? "inventory-qty-zero" : "";
    tdQty.textContent = `${item.quantity} ${item.unit}`;
    tr.appendChild(tdQty);

    // Actions
    const tdActions = document.createElement("td");
    tdActions.className = "inventory-table-actions";
    const histBtn = createActionButton("\u{1F4CB}", "inventory-action-history", () => cbs.onViewTransactions(item));
    histBtn.title = t("inventory.action_history");
    tdActions.appendChild(histBtn);
    if (cbs.canWrite !== false) {
      const addBtn = createActionButton("+", "inventory-action-add", () => cbs.onAddStock(item));
      addBtn.title = t("inventory.action_add_stock");
      const useBtn = createActionButton("\u2212", "inventory-action-use", () => cbs.onConsumeStock(item));
      useBtn.title = t("inventory.action_use_stock");
      const plantBtn = createActionButton("\u{1F331}", "inventory-action-plant", () => cbs.onPlantFromStock(item));
      plantBtn.title = t("inventory.action_plant");
      const editBtn = createActionButton("\u270F\uFE0F", "inventory-action-edit", () => cbs.onEdit(item));
      editBtn.title = t("inventory.action_edit_item");
      const delBtn = createActionButton("\u{1F5D1}", "inventory-action-delete", () => cbs.onDelete(item));
      delBtn.title = t("inventory.action_delete_item");
      tdActions.prepend(addBtn, useBtn, plantBtn);
      tdActions.append(editBtn, delBtn);
    }
    tr.appendChild(tdActions);

    tbody.appendChild(tr);

    if (item.procurement_history.length > 0) {
      const sourcingRow = document.createElement("tr");
      const sourcingCell = document.createElement("td");
      sourcingCell.colSpan = 5;
      sourcingCell.className = "inventory-tx-notes";
      sourcingCell.append(
        createSourceSummaryLabel(),
        document.createTextNode(createProcurementSummary(item.procurement_history) ?? ""),
      );
      sourcingRow.appendChild(sourcingCell);
      tbody.appendChild(sourcingRow);
    }
  }
}

// ── Transaction history ───────────────────────────────────

export function renderTransactionHistory(
  container: HTMLElement,
  transactions: InventoryTransaction[],
): void {
  container.replaceChildren();
  if (transactions.length === 0) {
    const empty = document.createElement("p");
    empty.className = "journal-empty-text";
    empty.textContent = t("inventory.history_empty");
    container.appendChild(empty);
    return;
  }
  for (const tx of transactions) {
    const row = document.createElement("div");
    row.className = "inventory-tx-row";

    const deltaSpan = document.createElement("span");
    deltaSpan.className = tx.delta >= 0 ? "inventory-tx-delta-pos" : "inventory-tx-delta-neg";
    deltaSpan.textContent = tx.delta >= 0 ? `+${tx.delta}` : String(tx.delta);
    row.appendChild(deltaSpan);

    const reasonSpan = document.createElement("span");
    reasonSpan.className = "inventory-tx-reason";
    reasonSpan.textContent = transactionReasonLabel(tx.reason);
    row.appendChild(reasonSpan);

    if (tx.source_name) {
      const src = document.createElement("span");
      src.className = "inventory-tx-source";
      src.textContent = tx.source_name;
      row.appendChild(src);
    }

    const dateSpan = document.createElement("span");
    dateSpan.className = "inventory-tx-date";
    dateSpan.textContent = formatDate(tx.occurred_on);
    row.appendChild(dateSpan);

    if (tx.cost_minor !== null) {
      const cost = document.createElement("span");
      cost.className = "inventory-tx-cost";
      cost.textContent = formatCost(tx.cost_minor);
      row.appendChild(cost);
    }

    if (tx.notes) {
      const notes = document.createElement("div");
      notes.className = "inventory-tx-notes";
      notes.textContent = tx.notes.length > 100 ? tx.notes.slice(0, 100) + "\u2026" : tx.notes;
      row.appendChild(notes);
    }

    if (tx.actor_username) {
      const actor = document.createElement("span");
      actor.className = "inventory-tx-actor";
      actor.textContent = tx.actor_username;
      row.appendChild(actor);
    }

    container.appendChild(row);
  }
}

function formatCost(minor: number): string {
  return new Intl.NumberFormat(getLocaleTag(), {
    style: "currency",
    currency: "NOK",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(minor / 100);
}

// ── Item form ─────────────────────────────────────────────

export interface InventoryItemFormOpts {
  existing?: InventoryItem;
  plants?: Array<{ plt_id: string; name: string }>;
  onSubmit: (data: {
    plt_id: string | null;
    label: string;
    inventory_type: InventoryType;
    unit: string;
  }) => void | Promise<void>;
  onCancel: () => void;
}

export function createInventoryItemForm(opts: InventoryItemFormOpts): HTMLElement {
  const form = document.createElement("form");
  form.className = "inventory-form";

  const labelInput = addField(form, t("inventory.form_label"), "text", opts.existing?.label ?? "", "inv-label");
  labelInput.required = true;
  labelInput.maxLength = 200;

  const typeSelect = document.createElement("select");
  typeSelect.id = "inv-type";
  typeSelect.className = "form-select";
  for (const value of Object.keys(INVENTORY_TYPE_TRANSLATION_KEYS) as InventoryType[]) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = inventoryTypeLabel(value);
    if (value === (opts.existing?.inventory_type ?? "seed")) opt.selected = true;
    typeSelect.appendChild(opt);
  }
  addFieldWithEl(form, t("inventory.form_type"), typeSelect);

  const unitInput = addField(form, t("inventory.form_unit"), "text", opts.existing?.unit ?? "pcs", "inv-unit");
  unitInput.maxLength = 40;

  // Plant link
  const plantSelect = document.createElement("select");
  plantSelect.id = "inv-plant";
  plantSelect.className = "form-select";
  const noneOpt = document.createElement("option");
  noneOpt.value = "";
  noneOpt.textContent = t("inventory.form_none");
  plantSelect.appendChild(noneOpt);
  if (opts.plants) {
    for (const p of opts.plants) {
      const opt = document.createElement("option");
      opt.value = p.plt_id;
      opt.textContent = p.name;
      if (p.plt_id === opts.existing?.plt_id) opt.selected = true;
      plantSelect.appendChild(opt);
    }
  }
  addFieldWithEl(form, t("inventory.form_linked_plant"), plantSelect);

  // Buttons
  const btnRow = document.createElement("div");
  btnRow.className = "form-btn-row";
  const submitBtn = document.createElement("button");
  submitBtn.type = "submit";
  submitBtn.className = "btn-primary";
  submitBtn.textContent = opts.existing ? t("common.save") : t("common.create");
  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "btn-secondary";
  cancelBtn.textContent = t("common.cancel");
  cancelBtn.addEventListener("click", opts.onCancel);
  btnRow.append(submitBtn, cancelBtn);
  form.appendChild(btnRow);

  let pending = false;
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (pending) return;
    pending = true;
    submitBtn.disabled = true;
    cancelBtn.disabled = true;
    form.setAttribute("aria-busy", "true");
    void Promise.resolve(
      opts.onSubmit({
        plt_id: plantSelect.value || null,
        label: labelInput.value.trim(),
        inventory_type: typeSelect.value as InventoryType,
        unit: unitInput.value.trim() || "pcs",
      }),
    ).finally(() => {
      if (!form.isConnected) return;
      pending = false;
      submitBtn.disabled = false;
      cancelBtn.disabled = false;
      form.removeAttribute("aria-busy");
    });
  });

  return form;
}

// ── Stock transaction form ────────────────────────────────

export interface StockTransactionFormOpts {
  item: InventoryItem;
  mode: "add" | "consume" | "plant";
  plots?: Array<{ plot_id: string; zone_code: string }>;
  onSubmit: (data: {
    delta: number;
    reason: TransactionReason;
    source_name: string;
    cost_minor: number | null;
    occurred_on: string;
    storage_location: string;
    notes: string;
    plot_id?: string;
    create_journal: boolean;
  }) => void | Promise<void>;
  onCancel: () => void;
}

const ADD_REASONS: TransactionReason[] = ["purchased", "harvested", "divided", "gifted", "adjusted"];
const CONSUME_REASONS: TransactionReason[] = ["sowed", "planted", "disposed", "adjusted"];

export function createStockTransactionForm(opts: StockTransactionFormOpts): HTMLElement {
  const form = document.createElement("form");
  form.className = "inventory-form";
  const isAdd = opts.mode === "add";
  const isPlant = opts.mode === "plant";

  const qtyInput = addField(
    form,
    isAdd
      ? t("inventory.tx_qty_add", { unit: opts.item.unit })
      : isPlant
        ? t("inventory.tx_qty_plant", { unit: opts.item.unit })
        : t("inventory.tx_qty_use", { unit: opts.item.unit }),
    "number",
    "1",
    "inv-tx-qty",
  );
  qtyInput.min = "0.000001";
  qtyInput.step = "0.000001";
  if (!isAdd && opts.item.quantity > 0) {
    qtyInput.max = String(opts.item.quantity);
  }
  qtyInput.required = true;

  const reasonSelect = document.createElement("select");
  reasonSelect.id = "inv-tx-reason";
  reasonSelect.className = "form-select";
  const reasons = isAdd
    ? ADD_REASONS
    : isPlant
      ? (["planted", "sowed", "disposed", "adjusted"] as TransactionReason[])
      : CONSUME_REASONS;
  for (const r of reasons) {
    const opt = document.createElement("option");
    opt.value = r;
    opt.textContent = transactionReasonLabel(r);
    if (isPlant && r === "planted") opt.selected = true;
    reasonSelect.appendChild(opt);
  }
  addFieldWithEl(form, t("inventory.tx_reason"), reasonSelect);

  let sourceInput: HTMLInputElement | undefined;
  if (isAdd) {
    sourceInput = addField(form, t("inventory.tx_source"), "text", "", "inv-tx-source");
    sourceInput.maxLength = 200;
  }

  const dateInput = addField(form, t("inventory.tx_date"), "date", todayISO(), "inv-tx-date");
  dateInput.required = true;

  let costInput: HTMLInputElement | undefined;
  if (isAdd) {
    costInput = addField(form, t("inventory.tx_cost"), "number", "", "inv-tx-cost");
  }

  const storageInput = addField(form, t("inventory.tx_storage"), "text", "", "inv-tx-storage");
  storageInput.maxLength = 200;

  const notesArea = document.createElement("textarea");
  notesArea.id = "inv-tx-notes";
  notesArea.className = "form-textarea";
  notesArea.rows = 2;
  notesArea.maxLength = 2000;
  notesArea.placeholder = t("inventory.tx_notes_placeholder");
  addFieldWithEl(form, t("inventory.tx_notes"), notesArea);

  // Plant from stock: plot selector + journal checkbox
  let plotSelect: HTMLSelectElement | undefined;
  const supportsPlotAssignment = isPlant && opts.plots && opts.item.plt_id;
  if (supportsPlotAssignment && opts.plots) {
    plotSelect = document.createElement("select");
    plotSelect.id = "inv-tx-plot";
    plotSelect.className = "form-select";
    const noneOpt = document.createElement("option");
    noneOpt.value = "";
    noneOpt.textContent = t("inventory.tx_no_plot");
    plotSelect.appendChild(noneOpt);
    for (const p of opts.plots) {
      const opt = document.createElement("option");
      opt.value = p.plot_id;
      opt.textContent = `${p.plot_id} (${p.zone_code})`;
      plotSelect.appendChild(opt);
    }
    addFieldWithEl(form, t("inventory.tx_assign_plot"), plotSelect);
  }

  const journalLabel = document.createElement("label");
  journalLabel.className = "form-check-label";
  const journalCheck = document.createElement("input");
  journalCheck.type = "checkbox";
  journalCheck.id = "inv-tx-journal";
  journalCheck.checked = true;
  const journalText = document.createElement("span");
  journalText.textContent = t("inventory.tx_create_journal");
  journalLabel.append(journalCheck, journalText);
  form.appendChild(journalLabel);

  // Buttons
  const btnRow = document.createElement("div");
  btnRow.className = "form-btn-row";
  const submitBtn = document.createElement("button");
  submitBtn.type = "submit";
  submitBtn.className = "btn-primary";
  submitBtn.textContent = isAdd
    ? t("inventory.tx_submit_add")
    : isPlant
      ? t("inventory.tx_submit_plant")
      : t("inventory.tx_submit_use");
  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "btn-secondary";
  cancelBtn.textContent = t("common.cancel");
  cancelBtn.addEventListener("click", opts.onCancel);
  btnRow.append(submitBtn, cancelBtn);
  form.appendChild(btnRow);

  let pending = false;
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (pending) return;
    const qty = Number(qtyInput.value);
    if (!Number.isFinite(qty) || qty <= 0) return;
    const costVal = costInput?.value ? parseInt(costInput.value, 10) : null;
    const result: {
      delta: number;
      reason: TransactionReason;
      source_name: string;
      cost_minor: number | null;
      occurred_on: string;
      storage_location: string;
      notes: string;
      plot_id?: string;
      create_journal: boolean;
    } = {
      delta: isAdd ? qty : -qty,
      reason: reasonSelect.value as TransactionReason,
      source_name: sourceInput?.value.trim() ?? "",
      cost_minor: costVal,
      occurred_on: dateInput.value,
      storage_location: storageInput.value.trim(),
      notes: notesArea.value.trim(),
      create_journal: journalCheck.checked,
    };
    if (plotSelect?.value) result.plot_id = plotSelect.value;
    pending = true;
    submitBtn.disabled = true;
    cancelBtn.disabled = true;
    form.setAttribute("aria-busy", "true");
    void Promise.resolve(opts.onSubmit(result)).finally(() => {
      if (!form.isConnected) return;
      pending = false;
      submitBtn.disabled = false;
      cancelBtn.disabled = false;
      form.removeAttribute("aria-busy");
    });
  });

  return form;
}

// ── Source summary for plant detail ───────────────────────

export function renderInventorySourceSummary(
  container: HTMLElement,
  items: InventoryItem[],
  lastTx: InventoryTransaction | null,
  procurementItems: Array<{
    vendor_name: string;
    status: string;
    received_on: string | null;
    expected_on: string | null;
    ordered_on: string | null;
  }> = [],
  onViewAll: () => void,
): void {
  container.replaceChildren();
  const procurementHistory = collectPlantProcurementHistory(items, procurementItems);

  if (items.length === 0 && procurementHistory.length === 0) {
    const empty = document.createElement("p");
    empty.className = "inventory-summary-empty";
    empty.textContent = t("inventory.source_empty");
    container.appendChild(empty);
    return;
  }

  if (items.length > 0) {
    const totalQty = items.reduce((sum, it) => sum + it.quantity, 0);
    const unit = items[0]?.unit ?? "pcs";

    const stockLine = document.createElement("div");
    stockLine.className = "inventory-summary-line";
    const stockLabel = document.createElement("span");
    stockLabel.className = "inventory-summary-label";
    stockLabel.textContent = t("inventory.source_current_stock");
    const stockValue = document.createElement("span");
    stockValue.className = `inventory-summary-value${totalQty <= 0 ? " inventory-qty-zero" : ""}`;
    stockValue.textContent = `${totalQty} ${unit}`;
    stockLine.append(stockLabel, stockValue);
    container.appendChild(stockLine);
  }

  if (lastTx) {
    const txLine = document.createElement("div");
    txLine.className = "inventory-summary-line";
    const txLabel = document.createElement("span");
    txLabel.className = "inventory-summary-label";
    txLabel.textContent = t("inventory.source_last_transaction");
    const txValue = document.createElement("span");
    txValue.className = "inventory-summary-value";
    const reasonLabel = transactionReasonLabel(lastTx.reason);
    const deltaStr = lastTx.delta >= 0 ? `+${lastTx.delta}` : String(lastTx.delta);
    txValue.textContent = `${deltaStr} ${reasonLabel} (${formatDate(lastTx.occurred_on)})`;
    txLine.append(txLabel, txValue);
    container.appendChild(txLine);
  }

  const procurementLine = document.createElement("div");
  procurementLine.className = "inventory-summary-line";
  const procurementLabel = document.createElement("span");
  procurementLabel.className = "inventory-summary-label";
  procurementLabel.textContent = t("inventory.source_recent_procurement");
  const procurementValue = document.createElement("span");
  procurementValue.className = "inventory-summary-value";
  procurementValue.textContent = procurementHistory.length > 0
    ? createProcurementSummary(procurementHistory) ?? t("inventory.source_no_procurement")
    : t("inventory.source_no_procurement");
  procurementLine.append(procurementLabel, procurementValue);
  container.appendChild(procurementLine);

  const viewAllBtn = document.createElement("button");
  viewAllBtn.type = "button";
  viewAllBtn.className = "btn-link inventory-summary-viewall";
  viewAllBtn.textContent = t("inventory.source_view_all");
  viewAllBtn.addEventListener("click", onViewAll);
  container.appendChild(viewAllBtn);
}

function collectPlantProcurementHistory(
  items: InventoryItem[],
  procurementItems: Array<{
    vendor_name: string;
    status: string;
    received_on: string | null;
    expected_on: string | null;
    ordered_on: string | null;
  }>,
): InventoryProcurementHistoryEntry[] {
  const history: InventoryProcurementHistoryEntry[] = [];
  const seen = new Set<string>();

  for (const item of items) {
    for (const entry of item.procurement_history) {
      const key = `${entry.id}:${entry.updated_at_ms}`;
      if (seen.has(key)) continue;
      seen.add(key);
      history.push(entry);
    }
  }

  let syntheticId = -1;
  for (const item of procurementItems) {
    const key = [
      item.vendor_name.trim(),
      item.status.trim(),
      item.received_on || item.expected_on || item.ordered_on || "",
    ].join("|");
    if (!item.vendor_name.trim() || seen.has(key)) continue;
    seen.add(key);
    history.push({
      id: `synthetic-${Math.abs(syntheticId)}`,
      label: "",
      vendor_name: item.vendor_name,
      vendor_url: "",
      status: item.status,
      quantity: 0,
      unit: "",
      cost_minor: 0,
      currency: "NOK",
      ordered_on: item.ordered_on,
      expected_on: item.expected_on,
      received_on: item.received_on,
      updated_at_ms: 0,
    });
    syntheticId -= 1;
  }

  return history;
}

function createProcurementSummary(
  history: InventoryProcurementHistoryEntry[],
): string | null {
  if (history.length === 0) return null;
  const parts = history.slice(0, 2).map((entry) => {
    const vendor = entry.vendor_name || entry.label || t("inventory.source_no_procurement");
    const dateLabel = entry.received_on || entry.expected_on || entry.ordered_on;
    const status = t(`procurement.status_${entry.status}`);
    return dateLabel ? `${vendor} (${status} · ${formatDate(dateLabel)})` : `${vendor} (${status})`;
  });
  if (history.length > 2) {
    parts.push(t("inventory.source_more_entries", { count: history.length - 2 }));
  }
  return parts.join(" · ");
}

function createSourceSummaryLabel(): HTMLSpanElement {
  const label = document.createElement("span");
  label.className = "inventory-summary-label";
  label.textContent = `${t("inventory.source_recent_procurement")} `;
  return label;
}

// ── Helpers ───────────────────────────────────────────────

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

function addField(
  form: HTMLElement,
  label: string,
  type: string,
  value: string,
  id: string,
): HTMLInputElement {
  const input = document.createElement("input");
  input.type = type;
  input.id = id;
  input.className = "form-input";
  input.value = value;
  addFieldWithEl(form, label, input);
  return input;
}

function addFieldWithEl(form: HTMLElement, labelText: string, el: HTMLElement): void {
  const wrapper = document.createElement("label");
  wrapper.className = "form-field";
  const span = document.createElement("span");
  span.className = "form-label";
  span.textContent = labelText;
  wrapper.append(span, el);
  form.appendChild(wrapper);
}
