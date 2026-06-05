import type { AppContext } from "../core/appContext";
import { querySelect } from "../core/dom";
import type { GardenIssue } from "../core/models";
import { t } from "../core/i18n";
import {
  fetchIssuesApi,
  fetchIssueApi,
  fetchIssueHistoryApi,
  createIssueApi,
  updateIssueApi,
  resolveIssueApi,
  deleteIssueApi,
  getApiErrorMessage,
} from "../services/api";
import { renderIssueList, createIssueForm } from "../components/issues";
import { buildPlantNameMap } from "../core/plantNames";
import { renderPlotJournalPreview } from "../components/journalPreview";
import { confirmDialog, trapFocus } from "../components/dialogCore";
import { selectPlot } from "../components/plotInteractions";
import { showDiagnosePlantModal } from "../components/diagnosePlant";

let ctx: AppContext;

let issueItems: GardenIssue[] = [];
let issuesTotal = 0;
let issuesOffset = 0;
const ISSUES_PAGE_SIZE = 50;

export function setIssuesOffset(offset: number): void {
  issuesOffset = offset;
}

export function initIssuesTab(appCtx: AppContext): void {
  ctx = appCtx;

  document
    .getElementById("issues-add-btn")
    ?.addEventListener("click", () => {
      if (!ctx.ensureWriteAccess()) return;
      openIssueForm();
    });
  document
    .getElementById("issues-filter-status")
    ?.addEventListener("change", () => {
      issuesOffset = 0;
      void loadIssues();
    });
  document
    .getElementById("issues-filter-type")
    ?.addEventListener("change", () => {
      issuesOffset = 0;
      void loadIssues();
    });
  document
    .getElementById("issues-filter-severity")
    ?.addEventListener("change", () => {
      issuesOffset = 0;
      void loadIssues();
    });
}

export async function loadIssues(): Promise<void> {
  if (!ctx) return;
  try {
    const params: Record<string, string | number> = {
      limit: ISSUES_PAGE_SIZE,
      offset: issuesOffset,
    };
    const statusFilter = querySelect("issues-filter-status")?.value;
    if (statusFilter) params["status"] = statusFilter;
    const typeFilter = querySelect("issues-filter-type")?.value;
    if (typeFilter) params["issue_type"] = typeFilter;
    const severityFilter = querySelect("issues-filter-severity")?.value;
    if (severityFilter) params["severity"] = severityFilter;
    const result = await fetchIssuesApi(params);
    if (result.total > 0 && result.issues.length === 0 && issuesOffset > 0) {
      issuesOffset = Math.max(
        0,
        Math.floor((result.total - 1) / ISSUES_PAGE_SIZE) * ISSUES_PAGE_SIZE,
      );
      await loadIssues();
      return;
    }
    issueItems = result.issues;
    issuesTotal = result.total;
    renderIssuesView();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

function renderIssuesView(): void {
  const container = document.getElementById("issues-list");
  if (!container) return;
  const summary = document.getElementById("issues-summary");
  if (summary) {
    summary.textContent =
      issuesTotal === 0
        ? t("issues.summary_none")
        : t("issues.summary_count", { count: issuesTotal });
  }
  const plantNames = buildPlantNameMap(ctx.getPlants());
  const canWrite = ctx.canWrite();
  renderIssueList(container, issueItems, {
    onEdit: (issue) => void openIssueForm(issue),
    onResolve: (issue) => void handleResolveIssue(issue),
    onDelete: (issue) => void handleDeleteIssue(issue),
    onEmptyAction: canWrite ? () => openIssueForm() : undefined,
    onPlantClick: (pltId) => {
      ctx.focusPlantsInPlantsView([pltId]);
    },
    onPlotClick: (plotId) => {
      ctx.setActiveTab("map");
      void selectPlot(
        ctx.state,
        plotId,
        ctx.getPlotCallbacks(),
      );
    },
    canWrite,
  }, plantNames);
  ctx.renderDataExportBars();
  renderIssuesPagination();
}

function renderIssuesPagination(): void {
  const container = document.getElementById(
    "issues-pagination",
  );
  if (!container) return;
  container.replaceChildren();
  if (issuesTotal <= ISSUES_PAGE_SIZE) return;
  const page =
    Math.floor(issuesOffset / ISSUES_PAGE_SIZE) + 1;
  const totalPages = Math.ceil(
    issuesTotal / ISSUES_PAGE_SIZE,
  );
  const prev = document.createElement("button");
  prev.type = "button";
  prev.textContent = t("common.previous");
  prev.disabled = issuesOffset === 0;
  prev.addEventListener("click", () => {
    issuesOffset = Math.max(
      0,
      issuesOffset - ISSUES_PAGE_SIZE,
    );
    void loadIssues();
  });
  const info = document.createElement("span");
  info.textContent = t("common.page_of", {
    page,
    total: totalPages,
  });
  const next = document.createElement("button");
  next.type = "button";
  next.textContent = t("common.next");
  next.disabled =
    issuesOffset + ISSUES_PAGE_SIZE >= issuesTotal;
  next.addEventListener("click", () => {
    issuesOffset += ISSUES_PAGE_SIZE;
    void loadIssues();
  });
  container.append(prev, info, next);
}

export function openIssueForm(
  existingIssue?: GardenIssue,
): void {
  const readOnly = Boolean(existingIssue) && !ctx.canWrite();
  if (!existingIssue && !ctx.ensureWriteAccess()) return;
  const overlay = document.createElement("div");
  overlay.className = "modal";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");

  let releaseFocusTrap: (() => void) | null = null;
  const onEscape = (e: KeyboardEvent) => { if (e.key === "Escape") closeOverlay(); };
  const closeOverlay = () => {
    releaseFocusTrap?.();
    window.removeEventListener("keydown", onEscape);
    overlay.remove();
  };

  const form = createIssueForm({
    issue: existingIssue,
    readOnly,
    availablePlants: ctx.getPlants().map((p) => ({
      plt_id: p.plt_id,
      name: p.name,
    })),
    availablePlots: ctx
      .getPlots()
      .map((p) => p.plot_id)
      .sort(),
    ...(!existingIssue
        ? {
          onDiagnoseFromPhoto: () => {
            closeOverlay();
            showDiagnosePlantModal("", [], "", {
              onIssueCreated: (issueId) => {
                ctx.showToast(t("diagnose.issue_created"), "success");
                ctx.navigateToSubMode("issues");
                void loadIssues();
                void fetchIssueApi(issueId).then(
                  (createdIssue) => openIssueForm(createdIssue),
                  () => {},
                );
              },
              onClose: () => {},
            });
          },
        }
      : {}),
    onSave: async (data) => {
      try {
        const mediaFiles = ctx.extractPendingMediaFiles(
          data as Record<string, unknown>,
        );
        const issuePayload = ctx.withoutPendingMediaFiles(
          data as Record<string, unknown>,
        );
        let savedIssueId: string | null = existingIssue?.id ?? null;
        if (existingIssue) {
          await updateIssueApi(
            existingIssue.id,
            issuePayload,
          );
        } else if (!ctx.isOnline()) {
          await ctx.enqueueDraft(
            "issue_create",
            data as Record<string, unknown>,
          );
          ctx.showToast(
            t("offline.draft_saved"),
            "success",
          );
          void ctx.refreshOfflineIndicator();
          closeOverlay();
          return;
        } else {
          const created = await createIssueApi(
            issuePayload as Parameters<
              typeof createIssueApi
            >[0],
          );
          savedIssueId = created.id;
        }
        if (savedIssueId) {
          try {
            await ctx.uploadTargetMediaFiles(
              "issue",
              savedIssueId,
              mediaFiles,
            );
          } catch {
            ctx.showToast(
              t("media.issue_upload_partial"),
              "error",
            );
            closeOverlay();
            void loadIssues();
            return;
          }
        }
        ctx.showToast(
          t(
            existingIssue
              ? "issues.updated"
              : "issues.created",
          ),
          "success",
        );
        if (!existingIssue) {
          issuesOffset = 0;
        }
        closeOverlay();
        void loadIssues();
      } catch (err) {
        ctx.showToast(getApiErrorMessage(err), "error");
      }
    },
    onCancel: () => closeOverlay(),
  });
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeOverlay();
  });
  const dialog = document.createElement("div");
  dialog.className = "modal-content";
  dialog.appendChild(form);
  if (existingIssue) {
    ctx.attachReadonlyMediaSection(dialog, {
      targetType: "issue",
      targetId: existingIssue.id,
      emptyText: t("media.issue_empty"),
    });
    attachIssueHistorySection(dialog, existingIssue.id);
  }
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
  window.addEventListener("keydown", onEscape);
  releaseFocusTrap = trapFocus(overlay);
}

function issueHistoryEventLabel(
  kind: "created" | "updated" | "resolved",
): string {
  if (kind === "created")
    return t("issues.history_event_created");
  if (kind === "resolved")
    return t("issues.history_event_resolved");
  return t("issues.history_event_updated");
}

export function attachIssueHistorySection(
  dialog: HTMLElement,
  issueId: string,
): void {
  const section = document.createElement("section");
  section.className = "plant-journal-history";
  const heading = document.createElement("label");
  heading.textContent = t("issues.history_title");
  const container = document.createElement("div");
  container.className =
    "plant-journal-preview-container";
  section.append(heading, container);
  dialog.appendChild(section);

  const renderEmpty = () => {
    const empty = document.createElement("p");
    empty.className = "journal-empty-hint";
    empty.textContent = t("issues.history_empty");
    container.replaceChildren(empty);
  };

  void fetchIssueHistoryApi(issueId).then(
    (result) => {
      container.replaceChildren();
      if (
        result.issue_events.length === 0 &&
        result.journal_entries.length === 0
      ) {
        renderEmpty();
        return;
      }

      if (result.issue_events.length > 0) {
        const historyHeading =
          document.createElement("div");
        historyHeading.className =
          "journal-preview-heading";
        historyHeading.textContent = t(
          "issues.history_events_heading",
        );
        container.appendChild(historyHeading);

        result.issue_events.forEach((event) => {
          const row = document.createElement("div");
          row.className = "journal-preview-row";

          const icon = document.createElement("span");
          icon.className = "journal-preview-icon";
          icon.textContent =
            event.kind === "resolved"
              ? "\u2713"
              : event.kind === "updated"
                ? "\u270e"
                : "\u26a0";

          const text = document.createElement("span");
          text.className = "journal-preview-text";
          const parts = [
            issueHistoryEventLabel(event.kind),
          ];
          if (event.summary) parts.push(event.summary);
          else if (event.title) parts.push(event.title);
          text.textContent = parts.join(": ");

          const date = document.createElement("span");
          date.className = "journal-preview-date";
          date.textContent = new Date(
            event.at_ms,
          ).toLocaleDateString();

          row.append(icon, text, date);
          container.appendChild(row);
        });
      }

      if (result.journal_entries.length > 0) {
        const journalContainer =
          document.createElement("div");
        renderPlotJournalPreview(
          journalContainer,
          result.journal_entries,
          () => {
            /* no-op inside issue modal */
          },
        );
        container.appendChild(journalContainer);
      }
    },
    () => {
      const failed = document.createElement("p");
      failed.className = "journal-empty-hint";
      failed.textContent = t(
        "issues.history_load_failed",
      );
      container.replaceChildren(failed);
    },
  );
}

async function handleResolveIssue(
  issue: GardenIssue,
): Promise<void> {
  if (!ctx.ensureWriteAccess()) return;
  const ok = await confirmDialog(
    t("issues.confirm_resolve"),
    t("issues.action_resolve"),
  );
  if (!ok) return;
  try {
    await resolveIssueApi(issue.id);
    ctx.showToast(t("issues.resolved"), "success");
    void loadIssues();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

async function handleDeleteIssue(
  issue: GardenIssue,
): Promise<void> {
  if (!ctx.ensureWriteAccess()) return;
  const ok = await confirmDialog(
    t("issues.confirm_delete"),
    t("common.delete"),
  );
  if (!ok) return;
  try {
    await deleteIssueApi(issue.id);
    ctx.showToast(t("issues.deleted"), "success");
    void loadIssues();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}
