import type { Snapshot } from "../services/api";
import { createModal } from "../components/dialogCore";
import { appSlug } from "../core/branding";
import { t } from "../core/i18n";
import {
  deleteSnapshotApi,
  exportMapApi,
  listSnapshotsApi,
  restoreSnapshotApi,
  saveSnapshotApi,
} from "../services/api";

export interface SnapshotsContext {
  canWrite(): boolean;
  ensureWriteAccess(): boolean;
  showFetchError(err: unknown): void;
  confirmDialog(
    message: string,
    confirmLabel: string,
  ): Promise<boolean>;
  authorizeSensitiveAdminAction(
    actionLabel: string,
    defaultReason: string,
  ): Promise<string | null>;
  getActiveGardenId(): number | null;
  isCurrentGarden(gardenId: number | null): boolean;
  refreshRestoredSnapshotState(): Promise<void>;
  setMobileMapSheetOpen(
    sheetId: string | null,
  ): void;
}

let ctx: SnapshotsContext;

type SnapshotListMode = "dropdown" | "mobile" | "dialog";

export function initSnapshotsFeature(
  snapshotsCtx: SnapshotsContext,
): void {
  ctx = snapshotsCtx;
}

function renderSnapshotsList(
  container: HTMLElement,
  snapshots: Snapshot[],
  mode: SnapshotListMode,
): void {
  if (snapshots.length === 0) {
    const empty = document.createElement("div");
    empty.className = "dropdown-empty";
    empty.textContent = t("map.no_saved_layouts");
    container.replaceChildren(empty);
    return;
  }

  const rows = snapshots.map((snapshot) => {
    const row = document.createElement("div");
    row.className = `snapshot-row${mode === "mobile" ? " snapshot-row--mobile" : ""}`;

    const restoreBtn = document.createElement(
      "button",
    );
    restoreBtn.className = "snapshot-restore";
    restoreBtn.dataset["snapId"] = String(
      snapshot.id,
    );
    restoreBtn.disabled = !ctx.canWrite();

    const name = document.createElement("span");
    name.textContent = snapshot.name;

    const date = document.createElement("span");
    date.className = "snapshot-date";
    date.textContent = new Date(
      snapshot.created_at + "Z",
    ).toLocaleDateString();

    restoreBtn.append(name, date);
    restoreBtn.addEventListener("click", () => {
      void (async () => {
        const restored = await restoreLayout(
          snapshot.id,
        );
        if (!restored) return;
        if (mode === "dropdown") {
          container.hidden = true;
        } else if (mode === "mobile") {
          ctx.setMobileMapSheetOpen(null);
        } else {
          container.closest(".modal")?.remove();
        }
      })();
    });

    const deleteBtn = document.createElement(
      "button",
    );
    deleteBtn.className = "snapshot-delete";
    deleteBtn.dataset["snapDel"] = String(
      snapshot.id,
    );
    deleteBtn.title = t("common.delete");
    deleteBtn.disabled = !ctx.canWrite();
    deleteBtn.textContent = "\u00d7";
    deleteBtn.addEventListener("click", () => {
      void (async () => {
        if (!ctx.ensureWriteAccess()) return;
        const requestGardenId = ctx.getActiveGardenId();
        if (requestGardenId === null) return;
        const actionReason = await ctx.authorizeSensitiveAdminAction(
          t("common.delete"),
          `snapshot-delete:${snapshot.id}`,
        );
        if (!actionReason) return;
        if (!ctx.isCurrentGarden(requestGardenId)) return;
        try {
          await deleteSnapshotApi(snapshot.id, actionReason);
          if (!ctx.isCurrentGarden(requestGardenId)) return;
          await populateSnapshotsList(
            container,
            mode,
          );
        } catch (err) {
          if (!ctx.isCurrentGarden(requestGardenId)) return;
          ctx.showFetchError(err);
        }
      })();
    });

    row.append(restoreBtn, deleteBtn);
    return row;
  });
  container.replaceChildren(...rows);
}

async function populateSnapshotsList(
  container: HTMLElement,
  mode: SnapshotListMode,
): Promise<boolean> {
  const requestGardenId = ctx.getActiveGardenId();
  if (requestGardenId === null) return false;
  const snapshots = await listSnapshotsApi();
  if (!ctx.isCurrentGarden(requestGardenId)) return false;
  renderSnapshotsList(container, snapshots, mode);
  return true;
}

export async function refreshOpenSnapshotViews(): Promise<void> {
  const dropdown = document.getElementById(
    "snapshots-dropdown",
  );
  if (
    dropdown instanceof HTMLElement &&
    !dropdown.hidden
  ) {
    await populateSnapshotsList(
      dropdown,
      "dropdown",
    );
  }
  const mobileList = document.getElementById(
    "mobile-snapshots-list",
  );
  const mobileSheet = document.getElementById(
    "mobile-map-layouts-sheet",
  );
  if (
    mobileList instanceof HTMLElement &&
    mobileSheet instanceof HTMLElement &&
    mobileSheet.classList.contains(
      "mobile-map-sheet--open",
    )
  ) {
    await populateSnapshotsList(
      mobileList,
      "mobile",
    );
  }
  const dialogList = document.getElementById(
    "map-layouts-dialog-list",
  );
  if (dialogList instanceof HTMLElement) {
    await populateSnapshotsList(
      dialogList,
      "dialog",
    );
  }
}

export async function openMobileLayoutsSheet(): Promise<void> {
  const list = document.getElementById(
    "mobile-snapshots-list",
  );
  if (!(list instanceof HTMLElement)) return;
  try {
    if (await populateSnapshotsList(list, "mobile")) {
      ctx.setMobileMapSheetOpen(
        "mobile-map-layouts-sheet",
      );
    }
  } catch (err) {
    ctx.showFetchError(err);
  }
}

export async function openLayoutsDialog(): Promise<void> {
  document.getElementById("map-layouts-dialog")?.remove();
  const { dialog } = createModal(t("map.garden_layouts"), `
    <div class="modal-content map-layouts-dialog-content">
      <h3>${t("map.garden_layouts")}</h3>
      <div class="mobile-map-sheet-actions">
        <button id="map-layouts-dialog-save-btn" class="mobile-map-sheet-btn mobile-map-sheet-btn--primary" type="button">${t("map.save_current_layout")}</button>
      </div>
      <div id="map-layouts-dialog-list" class="mobile-snapshots-list" aria-live="polite"></div>
    </div>
  `);
  dialog.id = "map-layouts-dialog";
  const list = document.getElementById("map-layouts-dialog-list");
  const saveBtn = document.getElementById("map-layouts-dialog-save-btn");
  if (saveBtn instanceof HTMLButtonElement) {
    saveBtn.disabled = !ctx.canWrite();
    saveBtn.addEventListener("click", () => {
      void (async () => {
        const saved = await saveLayout();
        if (saved && list instanceof HTMLElement) {
          await populateSnapshotsList(list, "dialog");
        }
      })();
    });
  }
  if (!(list instanceof HTMLElement)) return;
  try {
    await populateSnapshotsList(list, "dialog");
  } catch (err) {
    ctx.showFetchError(err);
  }
}

export async function saveLayout(): Promise<boolean> {
  if (!ctx.ensureWriteAccess()) return false;
  const requestGardenId = ctx.getActiveGardenId();
  if (requestGardenId === null) return false;
  const name = prompt(t("map.layout_name_prompt"));
  if (!name) return false;
  if (!ctx.isCurrentGarden(requestGardenId)) return false;
  try {
    await saveSnapshotApi(name);
    if (!ctx.isCurrentGarden(requestGardenId)) return false;
    await refreshOpenSnapshotViews();
    return true;
  } catch (err) {
    if (!ctx.isCurrentGarden(requestGardenId)) return false;
    ctx.showFetchError(err);
    return false;
  }
}

export async function toggleSnapshotsDropdown(): Promise<void> {
  const dropdown = document.getElementById(
    "snapshots-dropdown",
  );
  if (!(dropdown instanceof HTMLElement)) return;

  if (!dropdown.hidden) {
    dropdown.hidden = true;
    return;
  }

  try {
    const populated = await populateSnapshotsList(
      dropdown,
      "dropdown",
    );
    if (populated) dropdown.hidden = false;
  } catch (err) {
    ctx.showFetchError(err);
  }
}

async function restoreLayout(
  id: string,
): Promise<boolean> {
  if (!ctx.ensureWriteAccess()) return false;
  const requestGardenId = ctx.getActiveGardenId();
  if (requestGardenId === null) return false;
  if (
    !(await ctx.confirmDialog(
      t("map.layout_restore_confirm"),
      t("map.layout_restore"),
    ))
  )
    return false;
  const actionReason = await ctx.authorizeSensitiveAdminAction(
    t("map.layout_restore"),
    `snapshot-restore:${id}`,
  );
  if (!actionReason) return false;
  if (!ctx.isCurrentGarden(requestGardenId)) return false;
  try {
    await restoreSnapshotApi(id, actionReason);
    if (!ctx.isCurrentGarden(requestGardenId)) return false;
    await ctx.refreshRestoredSnapshotState();
    return true;
  } catch (err) {
    if (!ctx.isCurrentGarden(requestGardenId)) return false;
    ctx.showFetchError(err);
    return false;
  }
}

export async function exportMap(): Promise<void> {
  const blob = await exportMapApi();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${appSlug()}-map.json`;
  a.click();
  URL.revokeObjectURL(url);
}
