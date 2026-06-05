import { GRID_COLS, GRID_ROWS } from "../core/constants";
// Note: GRID_COLS/GRID_ROWS used as defaults in picker maps when no dynamic dims available
import { buildInvitationLink } from "../core/urlSecurity";
import {
  formatPlotAssignmentMeaning,
  normalizePlotAssignmentId,
  resolvePlotAssignmentMeaning,
} from "../core/plotAssignmentMeanings";
import { clearChildren, setReviewedDynamicHtml, setStaticTemplateHtml } from "../core/sanitize";
import type { MediaLinkRef, PlotAssignment, PlotAssignmentMeaning } from "../services/api";
import {
  bulkUpdateSeenGrowingApi,
  deleteMediaAssetApi,
  fetchIssuesApi,
  fetchProcurementApi,
  fetchJournalEntriesApi,
  getApiErrorMessage,
  getPlantAssignmentsApi,
  listInventoryApi,
  listInventoryTransactionsApi,
  listMediaApi,
  removeMediaLinkApi,
  setPlantCoverApi,
  updatePlantApi,
  uploadMediaApi,
} from "../services/api";
import type { MediaAsset } from "../services/api";
import { enqueueDraft, isOnline } from "../services/offlineQueue";
import { t, formatPlantCategoryLabel } from "../core/i18n";
import { renderMediaGalleryLazy } from "./mediaGalleryLoader";
import { renderInventorySourceSummary } from "./inventory";
import { renderIssueHistoryPreview } from "./issues";
import { renderPlotJournalPreviewLazy } from "./journalPreviewLoader";
import { showToast } from "./toast";

export function createModal(ariaLabel: string, innerMarkup: string): {
  dialog: HTMLDivElement;
  close: () => void;
} {
  const dialog = document.createElement("div");
  dialog.className = "modal";
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-label", ariaLabel);
  setReviewedDynamicHtml(dialog, innerMarkup);
  document.body.appendChild(dialog);

  const releaseFocusTrap = trapFocus(dialog);

  const onEscape = (e: KeyboardEvent) => {
    if (e.key === "Escape") close();
  };
  const close = () => {
    releaseFocusTrap();
    dialog.remove();
    window.removeEventListener("keydown", onEscape);
  };
  window.addEventListener("keydown", onEscape);

  // Add a close button to the top-right of the modal content
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "close-btn modal-close-btn";
  closeBtn.setAttribute("aria-label", ariaLabel ? `Close ${ariaLabel}` : "Close");
  closeBtn.textContent = "\u00d7";
  closeBtn.addEventListener("click", close);
  const content = dialog.querySelector(".modal-content");
  if (content) {
    content.insertBefore(closeBtn, content.firstChild);
  } else {
    dialog.insertBefore(closeBtn, dialog.firstChild);
  }

  const firstFocusable = dialog.querySelector<HTMLElement>(
    'input, select, textarea, ' +
    'button:not(.modal-close-btn), [href], ' +
    '[tabindex]:not([tabindex="-1"])',
  );
  firstFocusable?.focus();

  return { dialog, close };
}

interface ShowDeleteMenuParams {
  x: number;
  y: number;
  onEdit?: () => void;
  onDelete: () => void;
}

interface ShowCreateDialogParams {
  row: number;
  col: number;
  onSubmit: (data: Record<string, string | number>) => Promise<void>;
}

interface ShowCreateZoneDialogParams {
  gridRows: number;
  gridCols: number;
  onSubmit: (data: {
    zone_code: string;
    zone_name: string;
    start_row: number;
    start_col: number;
    end_row: number;
    end_col: number;
    color?: string;
  }) => Promise<void>;
}

export interface AiPlantData {
  name: string;
  latin: string;
  category: string;
  bloom_month: string;
  color: string;
  hardiness: string;
  height_cm: number;
  light: string;
  link?: string;
}

export interface PlotOption {
  plot_id: string;
  zone_code: string;
  grid_row: number;
  grid_col: number;
  color: string | null;
}

export interface ShowCreatePlantDialogParams {
  nextId: string;
  availablePlots: PlotOption[];
  plotAssignmentMeanings: PlotAssignmentMeaning[];
  gridRows?: number;
  gridCols?: number;
  onSubmit: (
    data: Record<string, string | number | boolean | null>,
    plotIds: string[],
  ) => Promise<void>;
  onAiLookup: (q: string) => Promise<AiPlantData>;
  prefill?: Partial<AiPlantData> | undefined;
  preselectedPlotIds?: string[] | undefined;
  onIdentifyFromPhoto?: () => void;
}

interface EditPlantData {
  plt_id: string;
  name: string;
  latin: string;
  category: string;
  bloom_month: string;
  color: string;
  hardiness: string;
  height_cm: number | null;
  light: string;
  link: string;
  year_planted: string | null;
  deer_resistant: boolean;
  plot_ids?: string[];
  seen_growing?: boolean | null;
  seen_growing_date?: string | null;
  seen_growing_year?: number | null;
  seen_growing_is_current_year?: boolean;
  observed_this_year?: boolean;
  last_bloomed_on?: string | null;
  last_bloomed_year?: number | null;
  bloomed_this_year?: boolean;
  presence_status?: "present" | "mixed" | "gone";
  last_not_seen_year?: string | null;
}

interface ShowEditPlantDialogParams {
  plant: EditPlantData;
  availablePlots: PlotOption[];
  plotAssignmentMeanings: PlotAssignmentMeaning[];
  gridRows?: number;
  gridCols?: number;
  onSubmit: (
    fields: Record<string, string | number | boolean | null>,
    plotIds: string[],
  ) => Promise<void>;
  onDelete: (pltId: string) => void;
  onMediaChanged?: (targets: MediaLinkRef[]) => void;
  onReportIssue?: (pltId: string) => void;
  onAiUpdate?: (query: string) => Promise<AiPlantData>;
  onObservationChanged?: (pltId: string) => Promise<EditPlantData | null>;
}

function renderAuditRows(events: Array<{
    id: number;
    occurred_at_ms: number;
    actor_username: string;
    actor_role: string;
    actor_auth_type: string;
    method: string;
    path: string;
    status_code: number;
    remote_host: string;
    detail: string;
  }>): string {
  if (events.length === 0) {
    return "<tr><td colspan=\"9\">No audit events found.</td></tr>";
  }
  return events.map((event) => `
    <tr>
      <td>${esc(new Date(event.occurred_at_ms).toLocaleString())}</td>
      <td>${esc(event.actor_username)}</td>
      <td>${esc(event.actor_role)}</td>
      <td>${esc(event.actor_auth_type)}</td>
      <td>${esc(event.method)}</td>
      <td>${esc(event.path)}</td>
      <td>${event.status_code}</td>
      <td>${esc(event.remote_host || "-")}</td>
      <td>${esc(event.detail || "-")}</td>
    </tr>
  `).join("");
}

function renderSessionRows(sessions: Array<{
  token_hash: string;
  user_id: number;
  username: string;
  role: string;
  expires_at_ms: number;
  created_at_ms: number;
  last_seen_at_ms: number;
}>): string {
  if (sessions.length === 0) {
    return "<tr><td colspan=\"7\">No active sessions found.</td></tr>";
  }
  const ordered = [...sessions].sort((a, b) => b.last_seen_at_ms - a.last_seen_at_ms);
  return ordered.map((session) => `
    <tr>
      <td>${esc(session.username)}</td>
      <td>${esc(session.role)}</td>
      <td>${new Date(session.created_at_ms).toLocaleString()}</td>
      <td>${new Date(session.last_seen_at_ms).toLocaleString()}</td>
      <td>${new Date(session.expires_at_ms).toLocaleString()}</td>
      <td>${esc(session.token_hash.slice(0, 12))}...</td>
      <td>${session.user_id}</td>
    </tr>
  `).join("");
}

function formatDate(value: number | string | null | undefined): string {
  if (value === null || value === undefined) return "-";
  const parsed = typeof value === "number"
    ? new Date(value)
    : new Date(String(value));
  if (Number.isNaN(parsed.getTime())) return "-";
  return parsed.toLocaleString();
}

function roleOptions(selected: "viewer" | "editor" | "admin"): string {
  return (["viewer", "editor", "admin"] as const)
    .map((role) => `<option value="${role}"${role === selected ? " selected" : ""}>${role}</option>`)
    .join("");
}

function renderManagedUserRows(users: Array<{
  id: number;
  username: string;
  role: "viewer" | "editor" | "admin";
  is_active: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  deactivated_reason: string | null;
}>): string {
  if (users.length === 0) {
    return "<tr><td colspan=\"7\">No users found.</td></tr>";
  }
  const ordered = [...users].sort((a, b) => a.username.localeCompare(b.username));
  return ordered.map((user) => `
    <tr data-user-id="${user.id}">
      <td>${esc(user.username)}</td>
      <td>
        <select class="auth-user-role">
          ${roleOptions(user.role)}
        </select>
      </td>
      <td><input type="checkbox" class="auth-user-active"${user.is_active ? " checked" : ""} /></td>
      <td><input type="checkbox" class="auth-user-must-change"${user.must_change_password ? " checked" : ""} /></td>
      <td>${esc(formatDate(user.last_login_at))}</td>
      <td>${esc(user.deactivated_reason ?? "-")}</td>
      <td>
        <div class="button-row">
          <button type="button" class="auth-user-save">${t("common.save")}</button>
          <button type="button" class="auth-user-revoke">${t("admin.revoke_sessions_btn")}</button>
          <button type="button" class="auth-user-reset">${t("admin.issue_reset_btn")}</button>
        </div>
      </td>
    </tr>
  `).join("");
}

function renderInvitationRows(invitations: Array<{
  id: number;
  invitee_username: string;
  role: "viewer" | "editor" | "admin";
  status: "pending" | "accepted" | "revoked" | "expired";
  created_at_ms: number;
  expires_at_ms: number;
  accepted_at_ms: number | null;
  revoked_at_ms: number | null;
}>): string {
  if (invitations.length === 0) {
    return "<tr><td colspan=\"8\">No invitations found.</td></tr>";
  }
  const ordered = [...invitations].sort((a, b) => b.created_at_ms - a.created_at_ms);
  return ordered.map((invitation) => `
    <tr data-invitation-id="${invitation.id}">
      <td>${esc(invitation.invitee_username)}</td>
      <td>${esc(invitation.role)}</td>
      <td>${esc(invitation.status)}</td>
      <td>${esc(formatDate(invitation.created_at_ms))}</td>
      <td>${esc(formatDate(invitation.expires_at_ms))}</td>
      <td>${esc(formatDate(invitation.accepted_at_ms))}</td>
      <td>${esc(formatDate(invitation.revoked_at_ms))}</td>
      <td>
        ${invitation.status === "pending"
          ? `<button type="button" class="auth-invitation-revoke">${t("admin.revoke_btn")}</button>`
          : ""}
      </td>
    </tr>
  `).join("");
}

export function showDeleteMenu(params: ShowDeleteMenuParams): void {
  const { x, y, onEdit, onDelete } = params;
  const menu = document.createElement("div");
  menu.className = "context-menu";
  menu.setAttribute("role", "menu");
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;

  const editHtml = onEdit
    ? `<button class="menu-item menu-item-edit">${t("common.edit")}</button>`
    : "";
  setStaticTemplateHtml(menu, `${editHtml}<button class="menu-item menu-item-delete">${t("common.delete")}</button>`);

  document.body.appendChild(menu);

  // Clamp to viewport bounds
  requestAnimationFrame(() => {
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth) menu.style.left = `${window.innerWidth - rect.width - 8}px`;
    if (rect.bottom > window.innerHeight) menu.style.top = `${window.innerHeight - rect.height - 8}px`;
    if (rect.left < 0) menu.style.left = "8px";
    if (rect.top < 0) menu.style.top = "8px";
  });

  if (onEdit) {
    const editBtn = menu.querySelector<HTMLButtonElement>(".menu-item-edit");
    editBtn?.addEventListener("click", () => {
      menu.remove();
      onEdit();
    });
  }

  const deleteBtn = menu.querySelector<HTMLButtonElement>(".menu-item-delete");
  deleteBtn?.addEventListener("click", () => {
    menu.remove();
    document.removeEventListener("click", closeMenu);
    window.removeEventListener("keydown", onEscape);
    onDelete();
  });

  const closeMenu = () => {
    menu.remove();
    document.removeEventListener("click", closeMenu);
    window.removeEventListener("keydown", onEscape);
  };
  const onEscape = (e: KeyboardEvent) => {
    if (e.key === "Escape") closeMenu();
  };

  window.addEventListener("keydown", onEscape);
  setTimeout(() => document.addEventListener("click", closeMenu), 100);
}

export function showCreatePlotDialog(params: ShowCreateDialogParams): void {
  const { row, col, onSubmit } = params;

  const { dialog, close: closeDialog } = createModal(
    t("plots.create_title", { row, col }),
    `
    <div class="modal-content">
      <h3>${t("plots.create_title", { row, col })}</h3>
      <form id="create-plot-form">
        <label>${t("plots.form_plot_name")}:
          <input type="text" name="plot_id" required
            placeholder="${t("plots.plot_name_placeholder")}" />
        </label>
        <div class="button-row">
          <button type="submit">${t("common.create")}</button>
          <button type="button" id="cancel-create-plot">${t("common.cancel")}</button>
        </div>
      </form>
    </div>
  `,
  );

  const cancelBtn = dialog.querySelector<HTMLButtonElement>(
    "#cancel-create-plot",
  );
  cancelBtn?.addEventListener("click", closeDialog);

  const firstInput = dialog.querySelector<HTMLInputElement>(
    "input[name='plot_id']",
  );
  firstInput?.focus();

  const form = dialog.querySelector<HTMLFormElement>(
    "#create-plot-form",
  );
  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const plotId = firstInput?.value.trim().toUpperCase() ?? "";
    if (!plotId) return;

    const zoneMatch = plotId.match(/^([A-Za-z]+)(\d+)?$/);
    const zoneCode = zoneMatch?.[1] ?? plotId;
    const plotNumber = zoneMatch?.[2]
      ? parseInt(zoneMatch[2], 10)
      : 0;

    const data: Record<string, string | number> = {
      plot_id: plotId,
      zone_code: zoneCode,
      zone_name: zoneCode,
      plot_number: plotNumber,
      grid_row: row,
      grid_col: col,
      sub_zone: "",
      notes: "",
    };

    try {
      await onSubmit(data);
      closeDialog();
    } catch (err) {
      showToast(getApiErrorMessage(err), "error");
    }
  });
}

export function showCreateZoneDialog(params: ShowCreateZoneDialogParams): void {
  const { gridRows, gridCols, onSubmit } = params;
  const { dialog, close: closeDialog } = createModal(
    t("zones.create_title"),
    `
    <div class="modal-content">
      <h3>${t("zones.create_title")}</h3>
      <form id="create-zone-form">
        <div class="form-row-2">
          <label>${t("zones.form_code")}
            <input type="text" name="zone_code" maxlength="20" required placeholder="B" />
          </label>
          <label>${t("zones.form_name")}
            <input type="text" name="zone_name" maxlength="120" required placeholder="${t("zones.name_placeholder")}" />
          </label>
        </div>
        <div class="form-row-2">
          <label>${t("zones.form_from_row")}
            <input type="number" name="start_row" min="1" max="${gridRows}" step="1" value="1" required />
          </label>
          <label>${t("zones.form_from_col")}
            <input type="number" name="start_col" min="1" max="${gridCols}" step="1" value="1" required />
          </label>
        </div>
        <div class="form-row-2">
          <label>${t("zones.form_to_row")}
            <input type="number" name="end_row" min="1" max="${gridRows}" step="1" value="${Math.min(3, gridRows)}" required />
          </label>
          <label>${t("zones.form_to_col")}
            <input type="number" name="end_col" min="1" max="${gridCols}" step="1" value="${Math.min(3, gridCols)}" required />
          </label>
        </div>
        <label>${t("zones.form_color")}
          <input type="color" name="color" value="#4a7c59" />
        </label>
        <p class="modal-help-text">${t("zones.create_help")}</p>
        <div class="button-row">
          <button type="submit">${t("zones.create_button")}</button>
          <button type="button" id="cancel-create-zone">${t("common.cancel")}</button>
        </div>
      </form>
    </div>
    `,
  );

  dialog.querySelector<HTMLButtonElement>("#cancel-create-zone")?.addEventListener("click", closeDialog);
  const firstInput = dialog.querySelector<HTMLInputElement>("input[name='zone_code']");
  firstInput?.focus();

  dialog.querySelector<HTMLFormElement>("#create-zone-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget as HTMLFormElement;
    const zoneCode = (form.elements.namedItem("zone_code") as HTMLInputElement | null)?.value.trim() ?? "";
    const zoneName = (form.elements.namedItem("zone_name") as HTMLInputElement | null)?.value.trim() ?? "";
    const startRow = Number.parseInt((form.elements.namedItem("start_row") as HTMLInputElement | null)?.value ?? "", 10);
    const startCol = Number.parseInt((form.elements.namedItem("start_col") as HTMLInputElement | null)?.value ?? "", 10);
    const endRow = Number.parseInt((form.elements.namedItem("end_row") as HTMLInputElement | null)?.value ?? "", 10);
    const endCol = Number.parseInt((form.elements.namedItem("end_col") as HTMLInputElement | null)?.value ?? "", 10);
    const color = (form.elements.namedItem("color") as HTMLInputElement | null)?.value || "#4a7c59";
    if (!zoneCode || !zoneName) {
      showToast(t("map.zone_code_name_required"), "error");
      return;
    }
    if (!Number.isFinite(startRow) || !Number.isFinite(startCol) || !Number.isFinite(endRow) || !Number.isFinite(endCol)) {
      showToast(t("map.zone_bounds_required"), "error");
      return;
    }
    if (startRow > endRow || startCol > endCol) {
      showToast(t("map.zone_bounds_invalid"), "error");
      return;
    }
    try {
      await onSubmit({
        zone_code: zoneCode,
        zone_name: zoneName,
        start_row: startRow,
        start_col: startCol,
        end_row: endRow,
        end_col: endCol,
        color,
      });
      closeDialog();
    } catch (err) {
      showToast(getApiErrorMessage(err), "error");
    }
  });
}

export function showCreatePlantDialog(
  params: ShowCreatePlantDialogParams,
): void {
  const { nextId, availablePlots, onSubmit, onAiLookup, prefill } = params;
  const selectedPlotIds = new Set<string>(params.preselectedPlotIds ?? []);
  const defaultYearPlanted = String(new Date().getFullYear());

  const { dialog, close: closeDialog } = createModal(t("plants.search_modal_title"), `
    <div class="modal-content modal-content-wide">
      <h3>${t("plants.search_modal_title")}</h3>
      <div class="ai-lookup-section">
        <label>${t("plants.form_ai_label")}:
          <div class="ai-lookup-row">
            <input type="text" id="ai-plant-query"
              placeholder="e.g. Lavendel, Rhododendron..." autocomplete="off" />
            <button type="button" id="ai-lookup-btn" class="btn-ai">
              ${t("plants.form_ai_button")}
            </button>
            <button type="button" id="identify-from-photo-btn" class="btn-ai">
              ${t("identify.title")}
            </button>
          </div>
        </label>
        <div id="ai-lookup-status" class="ai-status" hidden></div>
      </div>
      <form id="create-plant-form">
        <input type="hidden" name="plt_id" value="${nextId}" />
        <label>${t("plants.form_name")}:
          <input type="text" name="name" required />
        </label>
        <label>${t("plants.form_latin")}:
          <input type="text" name="latin" />
        </label>
        <label>${t("plants.form_category")}:
          <select name="category">
            <option value="" disabled selected>${t("plants.form_category_select")}</option>
            <option value="løk">${formatPlantCategoryLabel("løk")}</option>
            <option value="frø">${formatPlantCategoryLabel("frø")}</option>
            <option value="busker">${formatPlantCategoryLabel("busker")}</option>
            <option value="baerbusker">${formatPlantCategoryLabel("baerbusker")}</option>
            <option value="trær">${formatPlantCategoryLabel("trær")}</option>
            <option value="stauder">${formatPlantCategoryLabel("stauder")}</option>
            <option value="grønnsaker">${formatPlantCategoryLabel("grønnsaker")}</option>
            <option value="urter">${formatPlantCategoryLabel("urter")}</option>
            <option value="klatreplanter">${formatPlantCategoryLabel("klatreplanter")}</option>
            <option value="stueplanter">${formatPlantCategoryLabel("stueplanter")}</option>
            <option value="sukkulenter">${formatPlantCategoryLabel("sukkulenter")}</option>
            <option value="orkidéer">${formatPlantCategoryLabel("orkidéer")}</option>
            <option value="prydgress">${formatPlantCategoryLabel("prydgress")}</option>
          </select>
        </label>
        <div class="form-row-2">
          <label>${t("plants.form_bloom")}:
            <input type="text" name="bloom_month"
              placeholder="e.g. mai-juni" />
          </label>
          <label>${t("plants.form_color")}:
            <input type="text" name="color" />
          </label>
        </div>
        <div class="form-row-2">
          <label>${t("plants.form_hardiness")}:
            <input type="text" name="hardiness"
              placeholder="e.g. H6" />
          </label>
          <label>${t("plants.form_height")}:
            <input type="number" name="height_cm" min="0" />
          </label>
        </div>
        <div class="form-row-2">
          <label>${t("plants.form_light")}:
            <input type="text" name="light"
              placeholder="e.g. sol, halvskygge" />
          </label>
          <label>${t("plants.form_year_planted")}:
            <input type="text" name="year_planted"
              value="${defaultYearPlanted}"
              placeholder="e.g. 2025 or 2024, 2025" />
          </label>
        </div>
        <label>${t("plants.form_link")}:
          <input type="url" name="link" placeholder="https://..." />
        </label>
        <label class="checkbox-label">
          <input type="checkbox" name="deer_resistant" />
          ${t("plants.form_deer_resistant")}
        </label>
        <div class="plot-assign-section">
          <label>${t("plants.form_plot_section")}:
            <div class="plot-search-row">
              <input type="text" id="plot-assign-search"
                placeholder="${t("plants.form_plot_placeholder")}"
                autocomplete="off" />
            </div>
          </label>
          <p id="plot-assign-hint" class="plot-assign-hint">${t("plots.assign_hint")}</p>
          <div id="plot-assign-dropdown" class="plot-assign-dropdown"
            hidden></div>
          <div id="plot-assign-chips" class="plot-assign-chips"></div>
          <div id="plot-picker-map" class="plot-picker-map"></div>
        </div>
        <div class="button-row">
          <button type="submit">${t("plants.form_submit_create")}</button>
          <button type="button" id="cancel-create-plant">${t("plants.form_cancel")}</button>
        </div>
      </form>
    </div>
  `);

  const cancelBtn = dialog.querySelector<HTMLButtonElement>(
    "#cancel-create-plant",
  );
  cancelBtn?.addEventListener("click", closeDialog);

  const aiInput = dialog.querySelector<HTMLInputElement>(
    "#ai-plant-query",
  );
  const aiBtn = dialog.querySelector<HTMLButtonElement>(
    "#ai-lookup-btn",
  );
  const aiStatus = dialog.querySelector<HTMLDivElement>(
    "#ai-lookup-status",
  );

  const identifyPhotoBtn = dialog.querySelector<HTMLButtonElement>(
    "#identify-from-photo-btn",
  );
  if (params.onIdentifyFromPhoto) {
    const cb = params.onIdentifyFromPhoto;
    identifyPhotoBtn?.addEventListener("click", () => {
      closeDialog();
      cb();
    });
  } else {
    identifyPhotoBtn?.remove();
  }

  const doAiLookup = async () => {
    const q = aiInput?.value.trim() ?? "";
    if (q.length < 2) return;
    if (!aiStatus || !aiBtn) return;

    aiBtn.disabled = true;
    aiBtn.textContent = t("plants.form_ai_searching");
    aiStatus.textContent = t("plants.form_ai_searching");
    aiStatus.className = "ai-status";
    aiStatus.hidden = false;

    try {
      const result = await onAiLookup(q);
      fillFormFromAi(dialog, result);
      aiStatus.textContent = t("plants.form_ai_filled");
      aiStatus.classList.add("ai-status-ok");
    } catch (err) {
      aiStatus.textContent = getApiErrorMessage(err);
      aiStatus.classList.add("ai-status-err");
    } finally {
      aiBtn.disabled = false;
      aiBtn.textContent = t("plants.form_ai_button");
    }
  };

  aiBtn?.addEventListener("click", () => void doAiLookup());
  aiInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void doAiLookup();
    }
  });

  aiInput?.focus();

  if (prefill) {
    const prefillData: AiPlantData = {
      name: prefill.name ?? "",
      latin: prefill.latin ?? "",
      category: prefill.category ?? "",
      bloom_month: prefill.bloom_month ?? "",
      color: prefill.color ?? "",
      hardiness: prefill.hardiness ?? "",
      height_cm: prefill.height_cm ?? 0,
      light: prefill.light ?? "",
    };
    if (prefill.link) prefillData.link = prefill.link;
    fillFormFromAi(dialog, prefillData);
    if (aiInput && prefill.name) {
      aiInput.value = prefill.name;
    }
  }

  wirePlotAssign(
    dialog,
    availablePlots,
    selectedPlotIds,
    params.plotAssignmentMeanings,
    params.gridRows,
    params.gridCols,
  );

  const form = dialog.querySelector<HTMLFormElement>(
    "#create-plant-form",
  );
  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const formData = new FormData(form);
    const data: Record<string, string | number | boolean | null> = {};
    formData.forEach((value, key) => {
      if (key === "deer_resistant") return;
      const str = (value as string).trim();
      if (key === "height_cm") {
        data[key] = str ? parseInt(str, 10) : null;
      } else if (key === "year_planted") {
        data[key] = str || null;
      } else {
        data[key] = str;
      }
    });
    const drCb = form.querySelector<HTMLInputElement>(
      "input[name='deer_resistant']",
    );
    data["deer_resistant"] = drCb?.checked ?? false;

    try {
      await onSubmit(data, [...selectedPlotIds]);
      closeDialog();
    } catch (err) {
      showToast(getApiErrorMessage(err), "error");
    }
  });
}

export function showEditPlantDialog(
  params: ShowEditPlantDialogParams,
): void {
  const { plant, availablePlots, onSubmit, onDelete } = params;
  const selectedPlotIds = new Set<string>(plant.plot_ids ?? []);
  let overallSeenGrowing = plant.seen_growing ?? null;
  let overallSeenGrowingDate = plant.seen_growing_date ?? null;
  let overallSeenGrowingIsCurrentYear = plant.seen_growing_is_current_year ?? false;

  const { dialog, close: closeDialog } = createModal(t("plants.edit_plant"), `
    <div class="modal-content modal-content-wide edit-plant-modal-content">
      <h3>${t("plants.edit_plant")}</h3>
      <form id="edit-plant-form">
        <input type="hidden" name="plt_id"
          value="${esc(plant.plt_id)}" />
        <label>${t("plants.form_name")}:
          <input type="text" name="name"
            value="${esc(plant.name)}" required />
        </label>
        <label>${t("plants.form_latin")}:
          <input type="text" name="latin"
            value="${esc(plant.latin)}" />
        </label>
        <label>${t("plants.form_category")}:
          <select name="category">
            ${categoryOptions(plant.category)}
          </select>
        </label>
        <div class="form-row-2">
          <label>${t("plants.form_bloom")}:
            <input type="text" name="bloom_month"
              value="${esc(plant.bloom_month)}"
              placeholder="e.g. mai-juni" />
          </label>
          <label>${t("plants.form_color")}:
            <input type="text" name="color"
              value="${esc(plant.color)}" />
          </label>
        </div>
        <div class="form-row-2">
          <label>${t("plants.form_hardiness")}:
            <input type="text" name="hardiness"
              value="${esc(plant.hardiness)}"
              placeholder="e.g. H6" />
          </label>
          <label>${t("plants.form_height")}:
            <input type="number" name="height_cm" min="0"
              value="${plant.height_cm ?? ""}" />
          </label>
        </div>
        <div class="form-row-2">
          <label>${t("plants.form_light")}:
            <input type="text" name="light"
              value="${esc(plant.light)}"
              placeholder="e.g. sol, halvskygge" />
          </label>
          <label>${t("plants.form_year_planted")}:
            <input type="text" name="year_planted"
              value="${esc(plant.year_planted ?? "")}"
              placeholder="e.g. 2025" />
          </label>
        </div>
        <label>${t("plants.form_link")}:
          <input type="url" name="link"
            value="${esc(plant.link)}"
            placeholder="https://..." />
        </label>
        <label class="checkbox-label">
          <input type="checkbox" name="deer_resistant"
            ${plant.deer_resistant ? "checked" : ""} />
          ${t("plants.form_deer_resistant")}
        </label>
        <div class="ai-update-section">
          <button type="button" id="ai-update-btn" class="btn-ai"
            style="width:100%;margin-top:var(--sp-2)">
            ${t("plants.form_ai_update_button")}
          </button>
          <div id="ai-update-status" class="ai-status" hidden></div>
        </div>
        <div class="plot-assign-section">
          <label>${t("plants.form_plot_section")}:
            <div class="plot-search-row">
              <input type="text" id="plot-assign-search"
                placeholder="${t("plants.form_plot_placeholder")}"
                autocomplete="off" />
            </div>
          </label>
          <p id="plot-assign-hint" class="plot-assign-hint">${t("plots.assign_hint")}</p>
          <div id="plot-assign-dropdown" class="plot-assign-dropdown"
            hidden></div>
          <div id="plot-assign-chips" class="plot-assign-chips"></div>
          <div id="plot-picker-map" class="plot-picker-map"></div>
        </div>
        <div class="plant-journal-history">
          <label>${t("plants.plot_seen_growing")}</label>
          <div id="plant-seen-growing-section"></div>
        </div>
        <div class="plant-journal-history">
          <label>${t("plants.bloom_observation")}</label>
          <div id="plant-bloom-observation-section"></div>
        </div>
        <div class="plant-journal-history">
          <label>${t("journal.title")}</label>
          <div id="plant-journal-preview" class="plant-journal-preview-container"></div>
        </div>
        <div class="plant-journal-history">
          <label>${t("issues.title")}</label>
          <div id="plant-issue-preview" class="plant-journal-preview-container"></div>
        </div>
        <div class="plant-journal-history">
          <label>${t("plants.mode_inventory")}</label>
          <div id="plant-inventory-summary" class="inventory-source-summary"></div>
        </div>
        <div class="plant-journal-history">
          <label>${t("media.photos")}</label>
          <div id="plant-media-gallery" class="plant-media-gallery"></div>
        </div>
        <div class="button-row">
          <button type="submit">${t("plants.form_submit_edit")}</button>
          <button type="button" id="cancel-edit-plant">${t("plants.form_cancel")}</button>
        </div>
        <button type="button" id="delete-edit-plant"
          class="btn-delete-plant">${t("common.delete")}</button>
        <button type="button" id="report-issue-plant"
          class="btn-secondary" style="margin-top:var(--sp-2);width:100%">${t("issues.report_from_plant")}</button>
      </form>
    </div>
  `);

  dialog.querySelector<HTMLButtonElement>(
    "#cancel-edit-plant",
  )?.addEventListener("click", closeDialog);

  dialog.querySelector<HTMLButtonElement>("#delete-edit-plant")
    ?.addEventListener("click", () => {
      void (async () => {
        const confirmed = await confirmDialog(
          t("plants.confirm_delete", { name: plant.name }),
          t("common.delete"),
        );
        if (!confirmed) return;
        closeDialog();
        onDelete(plant.plt_id);
      })();
    });

  const reportIssueBtn = dialog.querySelector<HTMLButtonElement>(
    "#report-issue-plant",
  );
  if (params.onReportIssue) {
    const cb = params.onReportIssue;
    reportIssueBtn?.addEventListener("click", () => {
      closeDialog();
      cb(plant.plt_id);
    });
  } else {
    reportIssueBtn?.remove();
  }

  const aiUpdateBtn = dialog.querySelector<HTMLButtonElement>(
    "#ai-update-btn",
  );
  const aiUpdateStatus = dialog.querySelector<HTMLElement>(
    "#ai-update-status",
  );
  if (params.onAiUpdate) {
    const aiCb = params.onAiUpdate;
    aiUpdateBtn?.addEventListener("click", () => {
      void (async () => {
        const currentName =
          dialog.querySelector<HTMLInputElement>("input[name='name']")
            ?.value.trim() ?? plant.name;
        const query = currentName || plant.name;
        if (!query || !aiUpdateBtn || !aiUpdateStatus) return;
        aiUpdateBtn.disabled = true;
        aiUpdateBtn.textContent = t("plants.form_ai_searching");
        aiUpdateStatus.textContent = t("plants.form_ai_searching");
        aiUpdateStatus.className = "ai-status";
        aiUpdateStatus.hidden = false;
        try {
          const result = await aiCb(query);
          fillFormFromAi(dialog, result);
          aiUpdateStatus.textContent = t("plants.form_ai_filled");
          aiUpdateStatus.classList.add("ai-status-ok");
        } catch (err) {
          aiUpdateStatus.textContent = getApiErrorMessage(err);
          aiUpdateStatus.classList.add("ai-status-err");
        } finally {
          aiUpdateBtn.disabled = false;
          aiUpdateBtn.textContent = t("plants.form_ai_update_button");
        }
      })();
    });
  } else {
    aiUpdateBtn?.remove();
  }

  const nameInput = dialog.querySelector<HTMLInputElement>(
    "input[name='name']",
  );
  nameInput?.focus();

  wirePlotAssign(
    dialog,
    availablePlots,
    selectedPlotIds,
    params.plotAssignmentMeanings,
    params.gridRows,
    params.gridCols,
  );

  // Load journal history for this plant
  const journalPreviewEl = dialog.querySelector<HTMLElement>("#plant-journal-preview");
  if (journalPreviewEl) {
    void fetchJournalEntriesApi({ plant_id: plant.plt_id, limit: 5, offset: 0 }).then(
      (result) => {
        renderPlotJournalPreviewLazy(journalPreviewEl, result.entries, () => {
          // "View all" is a no-op in this context — the full journal is on the Plants tab
        });
      },
      () => {
        // Silently ignore errors — journal preview is non-critical
      },
    );
  }

  const issuePreviewEl = dialog.querySelector<HTMLElement>("#plant-issue-preview");
  if (issuePreviewEl) {
    void fetchIssuesApi({ plant_id: plant.plt_id, limit: 5, offset: 0 }).then(
      (result) => {
        renderIssueHistoryPreview(issuePreviewEl, result.issues);
      },
      () => {
        // Silently ignore errors — issue preview is non-critical
      },
    );
  }

  // Load inventory summary for this plant
  const invSummaryEl = dialog.querySelector<HTMLElement>("#plant-inventory-summary");
  if (invSummaryEl) {
    void (async () => {
      try {
        const [result, procurementResult] = await Promise.all([
          listInventoryApi({ plt_id: plant.plt_id, limit: 10 }),
          fetchProcurementApi({ linked_plt_id: plant.plt_id, limit: 10 }),
        ]);
        let lastTx: import("../services/api").InventoryTransaction | null = null;
        const firstItem = result.items[0];
        if (firstItem) {
          try {
            const txResult = await listInventoryTransactionsApi(firstItem.id, { limit: 1 });
            if (txResult.transactions.length > 0) {
              lastTx = txResult.transactions[0] ?? null;
            }
          } catch {
            // Non-critical
          }
        }
        renderInventorySourceSummary(
          invSummaryEl,
          result.items,
          lastTx,
          procurementResult.items,
          () => {
          // View all: no-op in dialog context
          },
        );
      } catch {
        // Silently ignore errors
      }
    })();
  }

  const mediaGalleryEl = dialog.querySelector<HTMLElement>("#plant-media-gallery");
  if (mediaGalleryEl) {
    let assets: MediaAsset[] = [];
    let uploadProgressPct: number | null = null;
    const reloadMedia = async () => {
      const result = await listMediaApi({
        target_type: "plant",
        target_id: plant.plt_id,
        limit: 24,
      });
      assets = result.items;
      renderMedia();
    };
    const renderMedia = () => {
      void renderMediaGalleryLazy(mediaGalleryEl, {
        assets,
        emptyText: t("media.plant_empty"),
        canUpload: true,
        uploadProgressPct,
        setCoverLabel: t("media.set_cover"),
        deleteLabel: t("common.remove"),
        onFilesSelected: (files) => {
          void (async () => {
            try {
              if (!isOnline()) {
                await enqueueDraft("plant_media_upload", {
                  target_id: plant.plt_id,
                  media_files: [...files],
                });
                showToast(t("offline.draft_saved"), "success");
                return;
              }
              for (let i = 0; i < files.length; i += 1) {
                const file = files[i]!;
                await uploadMediaApi({
                  targetType: "plant",
                  targetId: plant.plt_id,
                  file,
                  onProgress: (pct) => {
                    uploadProgressPct = Math.round(((i + (pct / 100)) / files.length) * 100);
                    renderMedia();
                  },
                });
              }
              uploadProgressPct = null;
              await reloadMedia();
              params.onMediaChanged?.([{ target_type: "plant", target_id: plant.plt_id, sort_order: 0 }]);
              showToast(t("media.upload_complete", { count: files.length }));
            } catch (err) {
              uploadProgressPct = null;
              renderMedia();
              showToast(getApiErrorMessage(err), "error");
            }
          })();
        },
        onSetCoverAsset: (asset) => {
          void (async () => {
            try {
              await setPlantCoverApi(plant.plt_id, asset.asset_id);
              await reloadMedia();
              params.onMediaChanged?.([{ target_type: "plant", target_id: plant.plt_id, sort_order: 0 }]);
              showToast(t("media.cover_set"), "success");
            } catch (err) {
              showToast(getApiErrorMessage(err), "error");
            }
          })();
        },
        onDeleteAsset: (asset) => {
          void (async () => {
            const confirmed = await confirmDialog(
              t("media.remove_confirm", {
                name: asset.original_filename || t("media.untitled"),
              }),
              t("common.remove"),
            );
            if (!confirmed) return;
            try {
              await removeMediaLinkApi({
                assetId: asset.asset_id,
                targetType: "plant",
                targetId: plant.plt_id,
              });
              assets = assets.filter((candidate) => candidate.asset_id !== asset.asset_id);
              renderMedia();
              params.onMediaChanged?.([{ target_type: "plant", target_id: plant.plt_id, sort_order: 0 }]);
              showToast(t("media.removed"));
            } catch (err) {
              showToast(getApiErrorMessage(err), "error");
            }
          })();
        },
        onDeleteEverywhereAsset: (asset) => {
          void (async () => {
            const confirmed = await confirmDialog(
              t("media.delete_everywhere_confirm", {
                name: asset.original_filename || t("media.untitled"),
                count: asset.targets.length,
              }),
              t("media.delete_everywhere"),
            );
            if (!confirmed) return;
            try {
              await deleteMediaAssetApi(asset.asset_id);
              assets = assets.filter((candidate) => candidate.asset_id !== asset.asset_id);
              renderMedia();
              params.onMediaChanged?.(asset.targets);
              showToast(t("media.deleted_everywhere"));
            } catch (err) {
              showToast(getApiErrorMessage(err), "error");
            }
          })();
        },
        deleteEverywhereLabel: t("media.delete_everywhere"),
      });
    };
    renderMedia();
    void (async () => {
      try {
        await reloadMedia();
      } catch {
        // Silently ignore errors in the supplemental gallery.
      }
    })();
  }

  const seenGrowingSection = dialog.querySelector<HTMLElement>("#plant-seen-growing-section");
  const bloomObservationSection = dialog.querySelector<HTMLElement>("#plant-bloom-observation-section");

  const applyRefreshedObservationState = (refreshedPlant: EditPlantData | null): void => {
    if (!refreshedPlant) return;
    overallSeenGrowing = refreshedPlant.seen_growing ?? null;
    overallSeenGrowingDate = refreshedPlant.seen_growing_date ?? null;
    overallSeenGrowingIsCurrentYear = refreshedPlant.seen_growing_is_current_year ?? false;
    plant.seen_growing = overallSeenGrowing;
    plant.seen_growing_date = overallSeenGrowingDate;
    plant.seen_growing_year = refreshedPlant.seen_growing_year ?? observationYear(overallSeenGrowingDate);
    plant.seen_growing_is_current_year = overallSeenGrowingIsCurrentYear;
    plant.observed_this_year = refreshedPlant.observed_this_year ?? false;
    plant.last_bloomed_on = refreshedPlant.last_bloomed_on ?? null;
    plant.last_bloomed_year = refreshedPlant.last_bloomed_year ?? observationYear(plant.last_bloomed_on);
    plant.bloomed_this_year = refreshedPlant.bloomed_this_year ?? false;
    if (refreshedPlant.presence_status !== undefined) {
      plant.presence_status = refreshedPlant.presence_status;
    }
    plant.last_not_seen_year = refreshedPlant.last_not_seen_year ?? null;
    if (bloomObservationSection) {
      renderBloomObservationSummary(bloomObservationSection, plant);
    }
  };

  const syncObservationState = async (): Promise<void> => {
    if (!params.onObservationChanged) return;
    const refreshedPlant = await params.onObservationChanged(plant.plt_id);
    applyRefreshedObservationState(refreshedPlant);
  };

  // Load and render per-plot seen-growing status table
  if (seenGrowingSection) {
    const loadAndRenderSeenGrowing = async () => {
      try {
        const assignments = await getPlantAssignmentsApi(plant.plt_id);
        renderSeenGrowingTable(
          seenGrowingSection,
          plant.plt_id,
          assignments,
          loadAndRenderSeenGrowing,
          overallSeenGrowing,
          overallSeenGrowingDate,
          overallSeenGrowingIsCurrentYear,
          (seen, seenDate) => {
            overallSeenGrowing = seen;
            overallSeenGrowingDate = seenDate;
            overallSeenGrowingIsCurrentYear = isCurrentObservationYear(seenDate);
            plant.seen_growing = seen;
            plant.seen_growing_date = seenDate;
            plant.seen_growing_year = observationYear(seenDate);
            plant.seen_growing_is_current_year = overallSeenGrowingIsCurrentYear;
          },
          syncObservationState,
        );
      } catch {
        // Silently ignore errors — seen-growing section is non-critical
      }
    };
    void loadAndRenderSeenGrowing();
  }

  if (bloomObservationSection) {
    renderBloomObservationSummary(bloomObservationSection, plant);
  }

  const form = dialog.querySelector<HTMLFormElement>(
    "#edit-plant-form",
  );
  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const formData = new FormData(form);
    const fields: Record<string, string | number | boolean | null> = {};
    formData.forEach((value, key) => {
      if (key === "plt_id" || key === "deer_resistant") return;
      const str = (value as string).trim();
      if (key === "height_cm") {
        fields[key] = str ? parseInt(str, 10) : null;
      } else if (key === "year_planted") {
        fields[key] = str || null;
      } else {
        fields[key] = str;
      }
    });
    const drCb = form.querySelector<HTMLInputElement>(
      "input[name='deer_resistant']",
    );
    fields["deer_resistant"] = drCb?.checked ?? false;

    try {
      await onSubmit(fields, [...selectedPlotIds]);
      closeDialog();
    } catch (err) {
      showToast(getApiErrorMessage(err), "error");
    }
  });
}

interface ShowEditPlotDialogParams {
  plotId: string;
  currentColor: string | null;
  onSubmit: (newName: string, color: string | null) => Promise<void>;
}

const PLOT_COLOR_SWATCH_KEYS = [
  { key: "colors.green", value: "#6dbb6d" },
  { key: "colors.purple", value: "#a87fc4" },
  { key: "colors.orange", value: "#e0826a" },
  { key: "colors.yellow", value: "#d4c044" },
  { key: "colors.tan", value: "#c8c480" },
  { key: "colors.teal", value: "#58bfb0" },
  { key: "colors.blue", value: "#5a9fd4" },
  { key: "colors.pink", value: "#d0789a" },
];

export function showEditPlotDialog(
  params: ShowEditPlotDialogParams,
): void {
  const { plotId, currentColor, onSubmit } = params;
  let selectedColor = currentColor;

  const swatchesHtml = PLOT_COLOR_SWATCH_KEYS
    .map((s) => {
      const active =
        selectedColor === s.value ? " active" : "";
      return `<button type="button" class="color-swatch${active}" data-color="${s.value}" title="${t(s.key)}" style="background:${s.value}"></button>`;
    })
    .join("");

  const noneActive = !selectedColor ? " active" : "";

  const { dialog, close: closeDialog } = createModal(t("plots.edit_title"), `
    <div class="modal-content">
      <h3>${t("plots.edit_title")}</h3>
      <form id="edit-plot-form">
        <label>${t("plots.plot_name")}:
          <input type="text" name="plot_name" required
            value="${esc(plotId)}" />
        </label>
        <label>${t("plots.plot_color")}:
          <div class="color-swatches">
            <button type="button" class="color-swatch color-swatch-none${noneActive}" data-color="" title="${t("plots.zone_default")}">
              <span class="swatch-reset">&times;</span>
            </button>
            ${swatchesHtml}
          </div>
        </label>
        <div class="button-row">
          <button type="submit">${t("common.save")}</button>
          <button type="button" id="cancel-edit-plot">${t("common.cancel")}</button>
        </div>
      </form>
    </div>
  `);

  dialog.querySelector<HTMLButtonElement>(
    "#cancel-edit-plot",
  )?.addEventListener("click", closeDialog);

  const nameInput = dialog.querySelector<HTMLInputElement>(
    "input[name='plot_name']",
  );
  nameInput?.focus();
  nameInput?.select();

  dialog.querySelectorAll<HTMLButtonElement>(
    ".color-swatch",
  ).forEach((btn) => {
    btn.addEventListener("click", () => {
      dialog.querySelectorAll(".color-swatch").forEach(
        (s) => s.classList.remove("active"),
      );
      btn.classList.add("active");
      selectedColor = btn.dataset["color"] || null;
    });
  });

  const form = dialog.querySelector<HTMLFormElement>(
    "#edit-plot-form",
  );
  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const newName = nameInput?.value.trim().toUpperCase() ?? "";
    if (!newName) return;

    try {
      await onSubmit(newName, selectedColor);
      closeDialog();
    } catch (err) {
      showToast(getApiErrorMessage(err), "error");
    }
  });
}

interface ElevationEditorParams {
  elevations: Record<string, number>;
  overrides: Record<string, number>;
  zones: Record<string, string>;
  plots: PlotOption[];
  gridRows?: number;
  gridCols?: number;
  onSave: (overrides: Record<string, number | null>) => Promise<void>;
}

export function showElevationEditor(
  params: ElevationEditorParams,
): void {
  const { elevations, overrides, zones, plots, onSave } = params;
  const plotIds = Object.keys(elevations).sort();
  const selected = new Set<string>();

  const rows = plotIds.map((pid) => {
    const zone = zones[pid] ?? "";
    const lidarVal = elevations[pid]!;
    const ov = overrides[pid];
    const ovStr = ov != null ? String(ov) : "";
    return `<tr class="${ov != null ? "elev-overridden" : ""}" data-plot="${esc(pid)}">
      <td class="elev-check-cell"><input type="checkbox"
        class="elev-row-check" data-plot="${esc(pid)}" /></td>
      <td>${esc(pid)}</td>
      <td>${esc(zone)}</td>
      <td class="elev-lidar-cell">${lidarVal.toFixed(2)}</td>
      <td><input type="number" step="0.01" class="elev-override-input"
        data-plot="${esc(pid)}" value="${ovStr}"
        placeholder="${lidarVal.toFixed(2)}" /></td>
    </tr>`;
  }).join("");

  const { dialog, close: closeDialog } = createModal(
    t("map.edit_elevations_title"),
    `
    <div class="modal-content modal-content-wide elevation-editor">
      <div class="elevation-editor-header">
        <h3>${t("map.edit_elevations_title")}</h3>
        <button type="button" class="elev-close-btn"
          aria-label="${t("common.close")}">&times;</button>
      </div>
      <div class="elev-batch-bar" hidden>
        <span class="elev-batch-count">${t("map.elevations_selected", { count: 0 })}</span>
        <input type="number" step="0.01" class="elev-batch-input"
          placeholder="${t("map.set_elevation_placeholder")}" />
        <button type="button" class="elev-batch-apply">${t("common.apply")}</button>
        <button type="button" class="elev-batch-clear">${t("map.clear_selected")}</button>
      </div>
      <div class="elev-map-container"></div>
      <div class="elevation-editor-scroll">
        <table class="data-table elevation-editor-table">
          <thead>
            <tr>
              <th class="elev-check-cell"><input type="checkbox"
                class="elev-select-all" title="${t("map.select_all")}" /></th>
              <th>${t("map.elevation_col_plot")}</th>
              <th>${t("map.elevation_col_zone")}</th>
              <th>${t("map.elevation_col_lidar")}</th>
              <th>${t("map.elevation_col_override")}</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <div class="button-row">
        <button type="button" class="elev-reset-btn">${t("map.elevation_reset_all")}</button>
        <span style="flex:1"></span>
        <button type="button" class="elev-save-btn">${t("common.save")}</button>
      </div>
    </div>
  `,
  );

  const batchBar = dialog.querySelector<HTMLElement>(".elev-batch-bar")!;
  const batchCount = dialog.querySelector<HTMLElement>(
    ".elev-batch-count",
  )!;
  const batchInput = dialog.querySelector<HTMLInputElement>(
    ".elev-batch-input",
  )!;
  const selectAllCb = dialog.querySelector<HTMLInputElement>(
    ".elev-select-all",
  )!;

  function syncCheckboxes(): void {
    dialog.querySelectorAll<HTMLInputElement>(
      ".elev-row-check",
    ).forEach((cb) => {
      const pid = cb.dataset["plot"] ?? "";
      cb.checked = selected.has(pid);
    });
  }

  function updateBatchBar(): void {
    const n = selected.size;
    batchBar.hidden = n === 0;
    batchCount.textContent = t("map.elevations_selected", { count: n });
    selectAllCb.checked = n === plotIds.length && n > 0;
    selectAllCb.indeterminate = n > 0 && n < plotIds.length;
    dialog.querySelectorAll<HTMLElement>(
      "tr[data-plot]",
    ).forEach((tr) => {
      const pid = tr.dataset["plot"] ?? "";
      tr.classList.toggle("elev-selected", selected.has(pid));
    });
  }

  function onSelectionChanged(): void {
    syncCheckboxes();
    updateBatchBar();
    redrawMap();
  }

  selectAllCb.addEventListener("change", () => {
    if (selectAllCb.checked) {
      for (const pid of plotIds) selected.add(pid);
    } else {
      selected.clear();
    }
    onSelectionChanged();
  });

  dialog.querySelectorAll<HTMLInputElement>(
    ".elev-row-check",
  ).forEach((cb) => {
    cb.addEventListener("change", () => {
      const pid = cb.dataset["plot"] ?? "";
      if (cb.checked) {
        selected.add(pid);
      } else {
        selected.delete(pid);
      }
      onSelectionChanged();
    });
  });

  const mapContainer = dialog.querySelector<HTMLElement>(
    ".elev-map-container",
  )!;
  const elevPlots = plots.filter((p) => p.plot_id in elevations);
  renderElevPickerMap(
    mapContainer, elevPlots, selected, onSelectionChanged, elevations,
    params.gridRows, params.gridCols,
  );

  function redrawMap(): void {
    const el = mapContainer as
      HTMLElement & { _redraw?: () => void };
    el._redraw?.();
  }

  async function collectAndSave(): Promise<void> {
    const result: Record<string, number | null> = {};
    dialog.querySelectorAll<HTMLInputElement>(
      ".elev-override-input",
    ).forEach((inp) => {
      const pid = inp.dataset["plot"] ?? "";
      const val = inp.value.trim();
      if (val) {
        result[pid] = parseFloat(val);
      } else if (pid in overrides) {
        result[pid] = null;
      }
    });
    if (Object.keys(result).length === 0) {
      showToast(t("map.elevation_no_overrides"), "error");
      return;
    }
    await onSave(result);
  }

  dialog.querySelector<HTMLButtonElement>(".elev-batch-apply")
    ?.addEventListener("click", async () => {
      const val = batchInput.value.trim();
      if (!val) {
        showToast(t("map.elevation_enter_value"), "error");
        return;
      }
      for (const pid of selected) {
        const inp = dialog.querySelector<HTMLInputElement>(
          `.elev-override-input[data-plot="${pid}"]`,
        );
        if (inp) {
          inp.value = val;
          inp.closest("tr")?.classList.add("elev-overridden");
        }
      }
      try {
        await collectAndSave();
        closeDialog();
      } catch (err) {
        showToast(getApiErrorMessage(err), "error");
      }
    });

  dialog.querySelector<HTMLButtonElement>(".elev-batch-clear")
    ?.addEventListener("click", async () => {
      for (const pid of selected) {
        const inp = dialog.querySelector<HTMLInputElement>(
          `.elev-override-input[data-plot="${pid}"]`,
        );
        if (inp) {
          inp.value = "";
          inp.closest("tr")?.classList.remove("elev-overridden");
        }
      }
      try {
        await collectAndSave();
        closeDialog();
      } catch (err) {
        showToast(getApiErrorMessage(err), "error");
      }
    });

  dialog.querySelector<HTMLButtonElement>(".elev-close-btn")
    ?.addEventListener("click", closeDialog);

  dialog.querySelector<HTMLButtonElement>(".elev-reset-btn")
    ?.addEventListener("click", async () => {
      dialog.querySelectorAll<HTMLInputElement>(
        ".elev-override-input",
      ).forEach((inp) => { inp.value = ""; });
      dialog.querySelectorAll("tr.elev-overridden").forEach(
        (tr) => tr.classList.remove("elev-overridden"),
      );
      try {
        await collectAndSave();
        closeDialog();
      } catch (err) {
        showToast(getApiErrorMessage(err), "error");
      }
    });

  dialog.querySelectorAll<HTMLInputElement>(
    ".elev-override-input",
  ).forEach((inp) => {
    inp.addEventListener("input", () => {
      const tr = inp.closest("tr");
      if (!tr) return;
      if (inp.value.trim()) {
        tr.classList.add("elev-overridden");
      } else {
        tr.classList.remove("elev-overridden");
      }
    });
  });

  dialog.querySelector<HTMLButtonElement>(".elev-save-btn")
    ?.addEventListener("click", async () => {
      try {
        await collectAndSave();
        closeDialog();
      } catch (err) {
        showToast(getApiErrorMessage(err), "error");
      }
    });
}

function renderElevPickerMap(
  container: HTMLElement,
  plots: PlotOption[],
  selected: Set<string>,
  onToggle: () => void,
  elevations?: Record<string, number>,
  rows = GRID_ROWS,
  cols = GRID_COLS,
): void {
  const CW = 480;
  const CH = Math.round(CW * (rows / cols));
  const cellW = CW / cols;
  const cellH = CH / rows;
  const MIN_ZOOM = 1;
  const MAX_ZOOM = 5;
  const STEP = 0.15;
  const LABEL_ZOOM = 1.8;

  clearChildren(container);
  const canvas = document.createElement("canvas");
  canvas.width = CW;
  canvas.height = CH;
  container.appendChild(canvas);

  const hint = document.createElement("span");
  hint.className = "plot-picker-hint";
  hint.textContent = t("plots.picker_hint_select");
  container.appendChild(hint);

  const tooltip = document.createElement("div");
  tooltip.className = "plot-picker-tooltip elev-tooltip";
  tooltip.hidden = true;
  container.appendChild(tooltip);

  const posMap = new Map<string, PlotOption>();
  for (const p of plots) posMap.set(`${p.grid_row},${p.grid_col}`, p);

  const cam = { x: 0, y: 0, zoom: 1 };

  function clampZoom(z: number): number {
    return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
  }
  function clampPan(): void {
    const ww = CW * cam.zoom;
    const wh = CH * cam.zoom;
    cam.x = Math.min(0, Math.max(CW - ww, cam.x));
    cam.y = Math.min(0, Math.max(CH - wh, cam.y));
  }
  function zoomAt(cx: number, cy: number, z: number): void {
    const clamped = clampZoom(z);
    const ratio = clamped / cam.zoom;
    cam.x = cx - ratio * (cx - cam.x);
    cam.y = cy - ratio * (cy - cam.y);
    cam.zoom = clamped;
    clampPan();
  }
  function canvasXY(e: MouseEvent): { cx: number; cy: number } {
    const r = canvas.getBoundingClientRect();
    return {
      cx: (e.clientX - r.left) * (CW / r.width),
      cy: (e.clientY - r.top) * (CH / r.height),
    };
  }
  function hitTest(
    cx: number, cy: number,
  ): PlotOption | undefined {
    const col = Math.floor((cx - cam.x) / cam.zoom / cellW) + 1;
    const row = Math.floor((cy - cam.y) / cam.zoom / cellH) + 1;
    return posMap.get(`${row},${col}`);
  }

  function draw(): void {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, CW, CH);
    ctx.save();
    ctx.translate(cam.x, cam.y);
    ctx.scale(cam.zoom, cam.zoom);

    ctx.fillStyle = "#888";
    ctx.globalAlpha = 0.25;
    ctx.fillRect(5 * cellW, 8 * cellH, 12 * cellW, 8 * cellH);
    ctx.globalAlpha = 1;

    for (const p of plots) {
      const x = (p.grid_col - 1) * cellW;
      const y = (p.grid_row - 1) * cellH;
      const isSel = selected.has(p.plot_id);
      ctx.fillStyle = p.color
        ?? PICKER_ZONE_COLORS[p.zone_code] ?? "#888";
      ctx.globalAlpha = isSel ? 1 : 0.55;
      ctx.fillRect(x + 0.5, y + 0.5, cellW - 1, cellH - 1);
      if (isSel) {
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = 2 / cam.zoom;
        ctx.strokeRect(x + 1, y + 1, cellW - 2, cellH - 2);
        ctx.strokeStyle = "#111";
        ctx.lineWidth = 1 / cam.zoom;
        ctx.strokeRect(x, y, cellW, cellH);
      }
    }

    if (cam.zoom >= LABEL_ZOOM) {
      ctx.globalAlpha = 1;
      const fs = Math.min(cellW * 0.7, 8 / cam.zoom * 2);
      ctx.font = `bold ${fs}px sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (const p of plots) {
        const x = (p.grid_col - 1) * cellW + cellW / 2;
        const y = (p.grid_row - 1) * cellH + cellH / 2;
        ctx.fillStyle = "rgba(0,0,0,0.55)";
        ctx.fillText(p.plot_id, x + 0.3, y + 0.3);
        ctx.fillStyle = "#fff";
        ctx.fillText(p.plot_id, x, y);
      }
    }

    ctx.globalAlpha = 1;
    ctx.restore();
    hint.hidden = cam.zoom > 1;
  }

  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    e.stopPropagation();
    const { cx, cy } = canvasXY(e);
    const delta = e.deltaY > 0 ? -STEP : STEP;
    zoomAt(cx, cy, cam.zoom + delta);
    draw();
  }, { passive: false });

  let ptrDown = false;
  let ptrStart = { x: 0, y: 0 };
  let camStart = { x: 0, y: 0 };
  let dragged = false;

  canvas.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    ptrDown = true;
    dragged = false;
    const { cx, cy } = canvasXY(e);
    ptrStart = { x: cx, y: cy };
    camStart = { x: cam.x, y: cam.y };
    canvas.setPointerCapture(e.pointerId);
  });

  canvas.addEventListener("pointermove", (e) => {
    const { cx, cy } = canvasXY(e);
    if (ptrDown) {
      const dx = cx - ptrStart.x;
      const dy = cy - ptrStart.y;
      if (!dragged && (Math.abs(dx) > 3 || Math.abs(dy) > 3)) {
        dragged = true;
      }
      if (dragged) {
        cam.x = camStart.x + dx;
        cam.y = camStart.y + dy;
        clampPan();
        draw();
        canvas.style.cursor = "grabbing";
      }
      tooltip.hidden = true;
      return;
    }
    const plot = hitTest(cx, cy);
    canvas.style.cursor = plot
      ? "pointer"
      : cam.zoom > 1 ? "grab" : "";
    if (plot) {
      renderElevTooltip(tooltip, plot.plot_id, elevations);
      const cr = container.getBoundingClientRect();
      tooltip.hidden = false;
      tooltip.style.left = `${e.clientX - cr.left + 10}px`;
      tooltip.style.top = `${e.clientY - cr.top - 8}px`;
    } else {
      tooltip.hidden = true;
    }
  });

  canvas.addEventListener("pointerleave", () => {
    tooltip.hidden = true;
  });

  canvas.addEventListener("pointerup", (e) => {
    if (!ptrDown) return;
    ptrDown = false;
    canvas.releasePointerCapture(e.pointerId);
    canvas.style.cursor = "";
    if (!dragged) {
      const { cx, cy } = canvasXY(e);
      const plot = hitTest(cx, cy);
      if (plot) {
        if (selected.has(plot.plot_id)) {
          selected.delete(plot.plot_id);
        } else {
          selected.add(plot.plot_id);
        }
        draw();
        onToggle();
      }
    }
  });

  canvas.addEventListener("dblclick", (e) => {
    e.preventDefault();
    const { cx, cy } = canvasXY(e);
    zoomAt(cx, cy, cam.zoom + STEP * 3);
    draw();
  });

  let lastDist = 0;
  let lastCenter = { x: 0, y: 0 };
  canvas.addEventListener("touchstart", (e) => {
    if (e.touches.length !== 2) return;
    e.preventDefault();
    const t1 = e.touches[0]!;
    const t2 = e.touches[1]!;
    const dx = t1.clientX - t2.clientX;
    const dy = t1.clientY - t2.clientY;
    lastDist = Math.sqrt(dx * dx + dy * dy);
    const r = canvas.getBoundingClientRect();
    lastCenter = {
      x: ((t1.clientX + t2.clientX) / 2 - r.left) * (CW / r.width),
      y: ((t1.clientY + t2.clientY) / 2 - r.top) * (CH / r.height),
    };
  }, { passive: false });

  canvas.addEventListener("touchmove", (e) => {
    if (e.touches.length !== 2) return;
    e.preventDefault();
    const t1 = e.touches[0]!;
    const t2 = e.touches[1]!;
    const dx = t1.clientX - t2.clientX;
    const dy = t1.clientY - t2.clientY;
    const dist = Math.sqrt(dx * dx + dy * dy);
    const r = canvas.getBoundingClientRect();
    const center = {
      x: ((t1.clientX + t2.clientX) / 2 - r.left) * (CW / r.width),
      y: ((t1.clientY + t2.clientY) / 2 - r.top) * (CH / r.height),
    };
    zoomAt(center.x, center.y, cam.zoom * (dist / lastDist));
    cam.x += center.x - lastCenter.x;
    cam.y += center.y - lastCenter.y;
    clampPan();
    draw();
    lastDist = dist;
    lastCenter = center;
  }, { passive: false });

  draw();
  (container as HTMLElement & { _redraw: () => void })._redraw = draw;
}

function renderElevTooltip(
  el: HTMLElement,
  plotId: string,
  elevations?: Record<string, number>,
): void {
  const elev = elevations?.[plotId];
  if (elev == null) {
    el.textContent = plotId;
    return;
  }
  el.textContent = "";
  const nameSpan = document.createElement("span");
  nameSpan.className = "elev-tooltip-name";
  nameSpan.textContent = plotId;
  const valSpan = document.createElement("span");
  valSpan.className = "elev-tooltip-val";
  valSpan.textContent = `${elev.toFixed(2)} m`;
  el.appendChild(nameSpan);
  el.appendChild(valSpan);
}

function renderSeenGrowingTable(
  container: HTMLElement,
  pltId: string,
  assignments: PlotAssignment[],
  reload: () => Promise<void>,
  overallSeenGrowing: boolean | null = null,
  overallSeenGrowingDate: string | null = null,
  overallSeenGrowingIsCurrentYear = false,
  setOverallSeenGrowing: ((seen: boolean | null, seenDate: string | null) => void) | null = null,
  onObservationChanged: (() => Promise<void>) | null = null,
): void {
  clearChildren(container);
  const today = new Date().toISOString().slice(0, 10);

  const updateOverall = (
    seen: boolean | null,
    dateVal: string | null,
  ) => {
    void (async () => {
      try {
        await updatePlantApi(pltId, {
          seen_growing: seen,
          seen_growing_date: dateVal,
        });
        setOverallSeenGrowing?.(seen, dateVal);
        await onObservationChanged?.();
        await reload();
      } catch (err) {
        showToast(getApiErrorMessage(err), "error");
      }
    })();
  };

  const update = (
    plotId: string,
    seen: boolean | null,
    dateVal: string | null,
  ) => {
    void (async () => {
      try {
        await bulkUpdateSeenGrowingApi([
          {
            plot_id: plotId,
            plt_id: pltId,
            seen_growing: seen,
            seen_growing_date: dateVal,
          },
        ]);
        await onObservationChanged?.();
        await reload();
      } catch (err) {
        showToast(getApiErrorMessage(err), "error");
      }
    })();
  };

  const list = document.createElement("div");
  list.className = "seen-growing-list";

  const overallRow = document.createElement("div");
  overallRow.className = "seen-growing-row";

  const overallInfo = document.createElement("div");
  overallInfo.className = "seen-growing-info";

  const overallLabel = document.createElement("span");
  overallLabel.className = "seen-growing-plot";
  overallLabel.textContent = t("plants.overall_seen_growing");
  overallInfo.appendChild(overallLabel);

  if (overallSeenGrowing != null) {
    const overallBadge = document.createElement("span");
    overallBadge.className = overallSeenGrowing
      ? "seen-badge seen-badge-yes"
      : "seen-badge seen-badge-no";
    overallBadge.textContent = overallSeenGrowing
      ? t("plants.plot_seen_growing")
      : t("plants.plot_not_seen");
    if (overallSeenGrowingDate) {
      overallBadge.textContent += ` (${overallSeenGrowingDate})`;
    }
    overallInfo.appendChild(overallBadge);
    overallInfo.appendChild(createObservationSeasonBadge(overallSeenGrowingIsCurrentYear));
  }
  overallRow.appendChild(overallInfo);

  const overallBtns = document.createElement("div");
  overallBtns.className = "seen-growing-btns";

  const overallYes = document.createElement("button");
  overallYes.type = "button";
  overallYes.className = "seen-btn seen-btn-yes";
  overallYes.title = t("plants.mark_seen_growing");
  overallYes.textContent = "\u2713";
  if (overallSeenGrowing === true && overallSeenGrowingIsCurrentYear) {
    overallYes.classList.add("seen-btn-active");
  }
  overallYes.addEventListener("click", () => updateOverall(true, today));
  overallBtns.appendChild(overallYes);

  const overallNo = document.createElement("button");
  overallNo.type = "button";
  overallNo.className = "seen-btn seen-btn-no";
  overallNo.title = t("plants.mark_not_seen");
  overallNo.textContent = "\u2717";
  if (overallSeenGrowing === false && overallSeenGrowingIsCurrentYear) {
    overallNo.classList.add("seen-btn-active");
  }
  overallNo.addEventListener("click", () => {
    const yearStr = prompt(
      t("plants.not_seen_year_prompt"),
      String(new Date().getFullYear()),
    );
    if (yearStr === null) return;
    updateOverall(false, yearStr.trim() || null);
  });
  overallBtns.appendChild(overallNo);

  if (overallSeenGrowing != null) {
    const overallClear = document.createElement("button");
    overallClear.type = "button";
    overallClear.className = "seen-btn seen-btn-clear";
    overallClear.title = t("plants.clear_seen_status");
    overallClear.textContent = "\u2715";
    overallClear.addEventListener("click", () => updateOverall(null, null));
    overallBtns.appendChild(overallClear);
  }

  overallRow.appendChild(overallBtns);
  list.appendChild(overallRow);

  if (assignments.length === 0) {
    container.appendChild(list);
    return;
  }

  for (const asgn of assignments) {
    const row = document.createElement("div");
    row.className = "seen-growing-row";

    const info = document.createElement("div");
    info.className = "seen-growing-info";

    const plotLabel = document.createElement("span");
    plotLabel.className = "seen-growing-plot";
    plotLabel.textContent = asgn.plot_id;
    info.appendChild(plotLabel);

    if (asgn.seen_growing != null) {
      const badge = document.createElement("span");
      badge.className = asgn.seen_growing
        ? "seen-badge seen-badge-yes"
        : "seen-badge seen-badge-no";
      badge.textContent = asgn.seen_growing
        ? t("plants.plot_seen_growing")
        : t("plants.plot_not_seen");
      if (asgn.seen_growing_date) {
        badge.textContent += ` (${asgn.seen_growing_date})`;
      }
      info.appendChild(badge);
      info.appendChild(createObservationSeasonBadge(asgn.seen_growing_is_current_year ?? false));
    }

    row.appendChild(info);

    const btns = document.createElement("div");
    btns.className = "seen-growing-btns";

    const btnYes = document.createElement("button");
    btnYes.type = "button";
    btnYes.className = "seen-btn seen-btn-yes";
    btnYes.title = t("plants.mark_seen_growing");
    btnYes.textContent = "\u2713";
    if (asgn.seen_growing === true && (asgn.seen_growing_is_current_year ?? false)) {
      btnYes.classList.add("seen-btn-active");
    }
    btnYes.addEventListener(
      "click",
      () => update(asgn.plot_id, true, today),
    );
    btns.appendChild(btnYes);

    const btnNo = document.createElement("button");
    btnNo.type = "button";
    btnNo.className = "seen-btn seen-btn-no";
    btnNo.title = t("plants.mark_not_seen");
    btnNo.textContent = "\u2717";
    if (asgn.seen_growing === false && (asgn.seen_growing_is_current_year ?? false)) {
      btnNo.classList.add("seen-btn-active");
    }
    btnNo.addEventListener("click", () => {
      const yearStr = prompt(
        t("plants.not_seen_year_prompt"),
        String(new Date().getFullYear()),
      );
      if (yearStr === null) return;
      update(asgn.plot_id, false, yearStr.trim() || null);
    });
    btns.appendChild(btnNo);

    if (asgn.seen_growing != null) {
      const btnClear = document.createElement("button");
      btnClear.type = "button";
      btnClear.className = "seen-btn seen-btn-clear";
      btnClear.title = t("plants.clear_seen_status");
      btnClear.textContent = "\u2715";
      btnClear.addEventListener(
        "click",
        () => update(asgn.plot_id, null, null),
      );
      btns.appendChild(btnClear);
    }

    row.appendChild(btns);
    list.appendChild(row);
  }

  container.appendChild(list);
}

function observationYear(rawDate: string | null | undefined): number | null {
  if (!rawDate) return null;
  const trimmed = rawDate.trim();
  if (trimmed.length < 4) return null;
  const year = Number.parseInt(trimmed.slice(0, 4), 10);
  return Number.isFinite(year) ? year : null;
}

function isCurrentObservationYear(rawDate: string | null | undefined): boolean {
  const year = observationYear(rawDate);
  if (year == null) return false;
  return year === new Date().getFullYear();
}

function createObservationSeasonBadge(isCurrentYear: boolean): HTMLElement {
  const badge = document.createElement("span");
  badge.className = "seen-badge seen-badge-neutral";
  badge.textContent = isCurrentYear
    ? t("plants.current_season")
    : t("plants.historical");
  return badge;
}

function renderBloomObservationSummary(
  container: HTMLElement,
  plant: EditPlantData,
): void {
  clearChildren(container);

  const row = document.createElement("div");
  row.className = "seen-growing-row";

  const info = document.createElement("div");
  info.className = "seen-growing-info";

  const label = document.createElement("span");
  label.className = "seen-growing-plot";
  label.textContent = t("plants.bloom_observation");
  info.appendChild(label);

  if (plant.last_bloomed_on) {
    const badge = document.createElement("span");
    badge.className = "seen-badge seen-badge-yes";
    badge.textContent = `${t("journal.event.bloomed")} (${plant.last_bloomed_on})`;
    info.appendChild(badge);
    info.appendChild(createObservationSeasonBadge(plant.bloomed_this_year ?? false));
  } else {
    const empty = document.createElement("span");
    empty.className = "text-muted";
    empty.textContent = t("plants.no_bloom_observation");
    info.appendChild(empty);
  }

  row.appendChild(info);
  container.appendChild(row);
}

export function esc(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

const CATEGORIES = [
  "løk", "frø", "busker", "baerbusker", "trær",
  "stauder", "grønnsaker", "urter", "klatreplanter",
  "stueplanter", "sukkulenter", "orkidéer", "prydgress",
];

function categoryOptions(selected: string): string {
  return CATEGORIES
    .map((c) => `<option value="${c}"${c === selected ? " selected" : ""}>${formatPlantCategoryLabel(c)}</option>`)
    .join("");
}

const PICKER_ZONE_COLORS: Record<string, string> = {
  B: "#6dbb6d", V: "#a87fc4", T: "#e0826a", R: "#d4c044",
  S: "#c8c480", P: "#58bfb0", D: "#5a9fd4", H: "#d0789a",
};

function renderPlotPickerMap(
  container: HTMLElement,
  plots: PlotOption[],
  selected: Set<string>,
  onToggle: () => void,
  rows = GRID_ROWS,
  cols = GRID_COLS,
): void {
  const CW = 264;
  const CH = Math.round(CW * (rows / cols));
  const cellW = CW / cols;
  const cellH = CH / rows;
  const MIN_ZOOM = 1;
  const MAX_ZOOM = 5;
  const STEP = 0.15;
  const LABEL_ZOOM = 2.5;

  clearChildren(container);
  const canvas = document.createElement("canvas");
  canvas.width = CW;
  canvas.height = CH;
  container.appendChild(canvas);

  const hint = document.createElement("span");
  hint.className = "plot-picker-hint";
  hint.textContent = t("plots.picker_hint_zoom");
  container.appendChild(hint);

  const tooltip = document.createElement("span");
  tooltip.className = "plot-picker-tooltip";
  tooltip.hidden = true;
  container.appendChild(tooltip);

  const posMap = new Map<string, PlotOption>();
  for (const p of plots) {
    posMap.set(`${p.grid_row},${p.grid_col}`, p);
  }

  const cam = { x: 0, y: 0, zoom: 1 };

  function clampZoom(z: number): number {
    return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
  }

  function clampPan(): void {
    const ww = CW * cam.zoom;
    const wh = CH * cam.zoom;
    cam.x = Math.min(0, Math.max(CW - ww, cam.x));
    cam.y = Math.min(0, Math.max(CH - wh, cam.y));
  }

  function zoomAt(cx: number, cy: number, z: number): void {
    const clamped = clampZoom(z);
    const ratio = clamped / cam.zoom;
    cam.x = cx - ratio * (cx - cam.x);
    cam.y = cy - ratio * (cy - cam.y);
    cam.zoom = clamped;
    clampPan();
  }

  function canvasXY(e: MouseEvent): { cx: number; cy: number } {
    const r = canvas.getBoundingClientRect();
    return {
      cx: (e.clientX - r.left) * (CW / r.width),
      cy: (e.clientY - r.top) * (CH / r.height),
    };
  }

  function hitTest(
    cx: number, cy: number,
  ): PlotOption | undefined {
    const col = Math.floor((cx - cam.x) / cam.zoom / cellW) + 1;
    const row = Math.floor((cy - cam.y) / cam.zoom / cellH) + 1;
    return posMap.get(`${row},${col}`);
  }

  function draw(): void {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, CW, CH);
    ctx.save();
    ctx.translate(cam.x, cam.y);
    ctx.scale(cam.zoom, cam.zoom);

    ctx.fillStyle = "#888";
    ctx.globalAlpha = 0.25;
    ctx.fillRect(5 * cellW, 8 * cellH, 12 * cellW, 8 * cellH);
    ctx.globalAlpha = 1;

    for (const p of plots) {
      const x = (p.grid_col - 1) * cellW;
      const y = (p.grid_row - 1) * cellH;
      const isSel = selected.has(p.plot_id);
      ctx.fillStyle = p.color
        ?? PICKER_ZONE_COLORS[p.zone_code] ?? "#888";
      ctx.globalAlpha = isSel ? 1 : 0.7;
      ctx.fillRect(x + 0.5, y + 0.5, cellW - 1, cellH - 1);
      if (isSel) {
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = 2 / cam.zoom;
        ctx.strokeRect(x + 1, y + 1, cellW - 2, cellH - 2);
        ctx.strokeStyle = "#111";
        ctx.lineWidth = 1 / cam.zoom;
        ctx.strokeRect(x, y, cellW, cellH);
      }
    }

    if (cam.zoom >= LABEL_ZOOM) {
      ctx.globalAlpha = 1;
      const fs = Math.min(cellW * 0.7, 8 / cam.zoom * 2);
      ctx.font = `bold ${fs}px sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (const p of plots) {
        const x = (p.grid_col - 1) * cellW + cellW / 2;
        const y = (p.grid_row - 1) * cellH + cellH / 2;
        ctx.fillStyle = "rgba(0,0,0,0.55)";
        ctx.fillText(p.plot_id, x + 0.3, y + 0.3);
        ctx.fillStyle = "#fff";
        ctx.fillText(p.plot_id, x, y);
      }
    }

    ctx.globalAlpha = 1;
    ctx.restore();

    hint.hidden = cam.zoom > 1;
  }

  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    e.stopPropagation();
    const { cx, cy } = canvasXY(e);
    const delta = e.deltaY > 0 ? -STEP : STEP;
    zoomAt(cx, cy, cam.zoom + delta);
    draw();
  }, { passive: false });

  let ptrDown = false;
  let ptrStart = { x: 0, y: 0 };
  let camStart = { x: 0, y: 0 };
  let dragged = false;

  canvas.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    ptrDown = true;
    dragged = false;
    const { cx, cy } = canvasXY(e);
    ptrStart = { x: cx, y: cy };
    camStart = { x: cam.x, y: cam.y };
    canvas.setPointerCapture(e.pointerId);
  });

  canvas.addEventListener("pointermove", (e) => {
    const { cx, cy } = canvasXY(e);
    if (ptrDown) {
      const dx = cx - ptrStart.x;
      const dy = cy - ptrStart.y;
      if (!dragged && (Math.abs(dx) > 3 || Math.abs(dy) > 3)) {
        dragged = true;
      }
      if (dragged) {
        cam.x = camStart.x + dx;
        cam.y = camStart.y + dy;
        clampPan();
        draw();
        canvas.style.cursor = "grabbing";
      }
      tooltip.hidden = true;
      return;
    }
    const plot = hitTest(cx, cy);
    canvas.style.cursor = plot
      ? "pointer"
      : cam.zoom > 1 ? "grab" : "";
    if (plot) {
      tooltip.textContent = plot.plot_id;
      const cr = container.getBoundingClientRect();
      tooltip.hidden = false;
      tooltip.style.left = `${e.clientX - cr.left + 10}px`;
      tooltip.style.top = `${e.clientY - cr.top - 8}px`;
    } else {
      tooltip.hidden = true;
    }
  });

  canvas.addEventListener("pointerleave", () => {
    tooltip.hidden = true;
  });

  canvas.addEventListener("pointerup", (e) => {
    if (!ptrDown) return;
    ptrDown = false;
    canvas.releasePointerCapture(e.pointerId);
    canvas.style.cursor = "";
    if (!dragged) {
      const { cx, cy } = canvasXY(e);
      const plot = hitTest(cx, cy);
      if (plot) {
        if (selected.has(plot.plot_id)) {
          selected.delete(plot.plot_id);
        } else {
          selected.add(plot.plot_id);
        }
        draw();
        onToggle();
      }
    }
  });

  canvas.addEventListener("dblclick", (e) => {
    e.preventDefault();
    const { cx, cy } = canvasXY(e);
    zoomAt(cx, cy, cam.zoom + STEP * 3);
    draw();
  });

  let lastDist = 0;
  let lastCenter = { x: 0, y: 0 };

  canvas.addEventListener("touchstart", (e) => {
    if (e.touches.length !== 2) return;
    e.preventDefault();
    const t1 = e.touches[0]!;
    const t2 = e.touches[1]!;
    const dx = t1.clientX - t2.clientX;
    const dy = t1.clientY - t2.clientY;
    lastDist = Math.sqrt(dx * dx + dy * dy);
    const r = canvas.getBoundingClientRect();
    const sx = CW / r.width;
    const sy = CH / r.height;
    lastCenter = {
      x: ((t1.clientX + t2.clientX) / 2 - r.left) * sx,
      y: ((t1.clientY + t2.clientY) / 2 - r.top) * sy,
    };
  }, { passive: false });

  canvas.addEventListener("touchmove", (e) => {
    if (e.touches.length !== 2) return;
    e.preventDefault();
    const t1 = e.touches[0]!;
    const t2 = e.touches[1]!;
    const dx = t1.clientX - t2.clientX;
    const dy = t1.clientY - t2.clientY;
    const dist = Math.sqrt(dx * dx + dy * dy);
    const r = canvas.getBoundingClientRect();
    const sx = CW / r.width;
    const sy = CH / r.height;
    const center = {
      x: ((t1.clientX + t2.clientX) / 2 - r.left) * sx,
      y: ((t1.clientY + t2.clientY) / 2 - r.top) * sy,
    };
    zoomAt(center.x, center.y, cam.zoom * (dist / lastDist));
    cam.x += center.x - lastCenter.x;
    cam.y += center.y - lastCenter.y;
    clampPan();
    draw();
    lastDist = dist;
    lastCenter = center;
  }, { passive: false });

  draw();

  (container as HTMLElement & { _redraw: () => void })._redraw = draw;
}

function wirePlotAssign(
  dialog: HTMLElement,
  plots: PlotOption[],
  selected: Set<string>,
  plotAssignmentMeanings: PlotAssignmentMeaning[],
  gridRows = GRID_ROWS,
  gridCols = GRID_COLS,
): void {
  const search = dialog.querySelector<HTMLInputElement>(
    "#plot-assign-search",
  );
  const dropdown = dialog.querySelector<HTMLElement>(
    "#plot-assign-dropdown",
  );
  const chips = dialog.querySelector<HTMLElement>(
    "#plot-assign-chips",
  );
  const hint = dialog.querySelector<HTMLElement>("#plot-assign-hint");
  const mapContainer = dialog.querySelector<HTMLElement>(
    "#plot-picker-map",
  );
  if (!search || !dropdown || !chips) return;

  function renderChips(): void {
    if (!chips) return;
    if (selected.size === 0) {
      const empty = document.createElement("span");
      empty.className = "plot-assign-empty";
      empty.textContent = t("plots.no_plots_assigned");
      chips.replaceChildren(empty);
      return;
    }
    const chipEls = [...selected].sort().map((id) => {
      const chip = document.createElement("span");
      chip.className = "plot-chip";
      chip.dataset["plot"] = id;
      const textWrap = document.createElement("span");
      textWrap.className = "plot-chip-copy";
      const idLabel = document.createElement("span");
      idLabel.className = "plot-chip-id";
      idLabel.textContent = id;
      textWrap.appendChild(idLabel);
      const meaning = resolvePlotAssignmentMeaning(id, plotAssignmentMeanings);
      const meaningText = formatPlotAssignmentMeaning(meaning);
      if (meaningText) {
        const note = document.createElement("span");
        note.className = "plot-chip-note";
        note.textContent = meaningText;
        textWrap.appendChild(note);
        chip.title = meaningText;
      }
      chip.appendChild(textWrap);

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "chip-remove";
      removeBtn.title = t("common.remove");
      removeBtn.textContent = "\u00d7";
      removeBtn.addEventListener("click", () => {
        selected.delete(id);
        renderChips();
        redrawMap();
      });
      chip.append(removeBtn);
      return chip;
    });
    chips.replaceChildren(...chipEls);
  }

  function redrawMap(): void {
    const el = mapContainer as
      (HTMLElement & { _redraw?: () => void }) | null;
    el?._redraw?.();
  }

  function addPlotId(id: string): void {
    const normalizedId = normalizePlotAssignmentId(id);
    if (!normalizedId) return;
    selected.add(normalizedId);
    renderChips();
    redrawMap();
    if (search) search.value = "";
    if (dropdown) dropdown.hidden = true;
    if (hint) {
      hint.textContent = t("plots.assign_hint");
    }
  }

  function showDropdown(query: string): void {
    if (!dropdown) return;
    const q = query.toLowerCase();
    const matches = plots
      .filter(
        (p) =>
          !selected.has(p.plot_id) &&
          p.plot_id.toLowerCase().includes(q),
      )
      .slice(0, 12);

    if (matches.length === 0) {
      dropdown.hidden = true;
      return;
    }

    const items = matches.map((plot) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "plot-dd-item";
      button.dataset["plot"] = plot.plot_id;
      button.textContent = plot.plot_id;
      button.addEventListener("click", () => addPlotId(plot.plot_id));
      return button;
    });

    dropdown.replaceChildren(...items);
    dropdown.hidden = false;
  }

  search.addEventListener("input", () => {
    const q = search.value.trim();
    if (q.length === 0) {
      dropdown.hidden = true;
      if (hint) {
        hint.textContent = t("plots.assign_hint");
      }
      return;
    }
    const meaning = resolvePlotAssignmentMeaning(q, plotAssignmentMeanings);
    const meaningText = formatPlotAssignmentMeaning(meaning);
    if (hint && meaningText) {
      hint.textContent = `Matches ${meaning?.pattern}: ${meaningText}`;
    } else if (hint) {
      hint.textContent = t("plots.assign_hint_custom");
    }
    showDropdown(q);
  });

  search.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const val = normalizePlotAssignmentId(search.value);
    if (!val) return;
    if (!selected.has(val)) addPlotId(val);
  });

  search.addEventListener("focus", () => {
    if (search.value.trim().length > 0) {
      showDropdown(search.value.trim());
    }
  });

  renderChips();

  if (mapContainer) {
    renderPlotPickerMap(mapContainer, plots, selected, renderChips, gridRows, gridCols);
  }
}

function fillFormFromAi(
  dialog: HTMLElement,
  plant: AiPlantData,
): void {
  const set = (name: string, value: string) => {
    const el = dialog.querySelector<HTMLInputElement>(
      `[name="${name}"]`,
    );
    if (el) el.value = value;
  };
  set("name", plant.name);
  set("latin", plant.latin);
  set("bloom_month", plant.bloom_month);
  set("color", plant.color);
  set("hardiness", plant.hardiness);
  set("height_cm", String(plant.height_cm ?? ""));
  set("light", plant.light);
  if (plant.link) set("link", plant.link);

  const select = dialog.querySelector<HTMLSelectElement>(
    "[name='category']",
  );
  if (select) select.value = plant.category;
}

export function trapFocus(container: HTMLElement): () => void {
  const selector = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
  const handler = (e: KeyboardEvent) => {
    if (e.key !== "Tab") return;
    const focusable = Array.from(
      container.querySelectorAll<HTMLElement>(selector),
    ).filter((el) => !el.hasAttribute("disabled"));
    if (focusable.length === 0) return;
    const first = focusable[0]!;
    const last = focusable[focusable.length - 1]!;
    if (e.shiftKey && document.activeElement === first) {
      last.focus();
      e.preventDefault();
    } else if (!e.shiftKey && document.activeElement === last) {
      first.focus();
      e.preventDefault();
    }
  };
  container.addEventListener("keydown", handler);
  return () => container.removeEventListener("keydown", handler);
}

export function confirmDialog(
  message: string,
  confirmLabel?: string,
): Promise<boolean> {
  return new Promise((resolve) => {
    const dialog = document.createElement("div");
    dialog.className = "modal";
    dialog.setAttribute("role", "alertdialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-label", t("common.ok"));

    const content = document.createElement("div");
    content.className = "modal-content confirm-dialog";

    const text = document.createElement("p");
    text.textContent = message;

    const actions = document.createElement("div");
    actions.className = "button-row";

    const confirmBtn = document.createElement("button");
    confirmBtn.type = "button";
    confirmBtn.className = "confirm-yes";
    confirmBtn.textContent = confirmLabel ?? t("common.ok");

    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "confirm-no";
    cancelBtn.textContent = t("common.cancel");

    actions.append(confirmBtn, cancelBtn);
    content.append(text, actions);
    dialog.appendChild(content);
    document.body.appendChild(dialog);

    const removeTrap = trapFocus(dialog);
    const close = (result: boolean) => {
      removeTrap();
      window.removeEventListener("keydown", onKey);
      dialog.remove();
      resolve(result);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close(false);
    };
    window.addEventListener("keydown", onKey);

    confirmBtn.addEventListener("click", () => close(true));
    cancelBtn.addEventListener("click", () => close(false));
    confirmBtn.focus();
  });
}

export function promptDialog(
  message: string,
  defaultValue?: string,
): Promise<string | null> {
  return new Promise((resolve) => {
    const { dialog, close } = createModal(t("common.ok"), `
      <div class="modal-content confirm-dialog">
        <p></p>
        <input type="text" class="prompt-dialog-input" />
        <div class="button-row">
          <button type="button" class="confirm-yes">${t("common.ok")}</button>
          <button type="button" class="confirm-no">${t("common.cancel")}</button>
        </div>
      </div>
    `);
    // Set text content safely (no innerHTML for user message)
    dialog.querySelector("p")!.textContent = message;
    const input = dialog.querySelector<HTMLInputElement>(".prompt-dialog-input")!;
    input.value = defaultValue ?? "";
    input.focus();
    input.select();
    const finish = (value: string | null) => { close(); resolve(value); };
    dialog.querySelector(".confirm-yes")!.addEventListener("click", () => finish(input.value));
    dialog.querySelector(".confirm-no")!.addEventListener("click", () => finish(null));
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(input.value); });
  });
}
