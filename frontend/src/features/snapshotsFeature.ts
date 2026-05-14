import type { Snapshot } from "../services/api";
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
  fetchPlots(): Promise<void>;
  fetchLayoutState(): Promise<void>;
  setMobileMapSheetOpen(
    sheetId: string | null,
  ): void;
}

let ctx: SnapshotsContext;

type SnapshotListMode = "dropdown" | "mobile";

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
        } else {
          ctx.setMobileMapSheetOpen(null);
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
        const actionReason = await ctx.authorizeSensitiveAdminAction(
          t("common.delete"),
          `snapshot-delete:${snapshot.id}`,
        );
        if (!actionReason) return;
        try {
          await deleteSnapshotApi(snapshot.id, actionReason);
          await populateSnapshotsList(
            container,
            mode,
          );
        } catch (err) {
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
): Promise<void> {
  const snapshots = await listSnapshotsApi();
  renderSnapshotsList(container, snapshots, mode);
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
}

export async function openMobileLayoutsSheet(): Promise<void> {
  const list = document.getElementById(
    "mobile-snapshots-list",
  );
  if (!(list instanceof HTMLElement)) return;
  try {
    await populateSnapshotsList(list, "mobile");
    ctx.setMobileMapSheetOpen(
      "mobile-map-layouts-sheet",
    );
  } catch (err) {
    ctx.showFetchError(err);
  }
}

export async function saveLayout(): Promise<boolean> {
  if (!ctx.ensureWriteAccess()) return false;
  const name = prompt(t("map.layout_name_prompt"));
  if (!name) return false;
  try {
    await saveSnapshotApi(name);
    await refreshOpenSnapshotViews();
    return true;
  } catch (err) {
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
    await populateSnapshotsList(
      dropdown,
      "dropdown",
    );
    dropdown.hidden = false;
  } catch (err) {
    ctx.showFetchError(err);
  }
}

async function restoreLayout(
  id: string,
): Promise<boolean> {
  if (!ctx.ensureWriteAccess()) return false;
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
  try {
    await restoreSnapshotApi(id, actionReason);
    await ctx.fetchPlots();
    await ctx.fetchLayoutState();
    return true;
  } catch (err) {
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
