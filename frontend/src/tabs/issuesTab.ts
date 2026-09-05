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
  getActiveGardenContext,
  diagnosePlantApi,
  validateAiPhotoUpload,
  AI_PHOTO_UPLOAD_ACCEPT,
  type DiagnosisCandidate,
} from "../services/api";
import { renderIssueList, createIssueForm } from "../components/issues";
import { buildPlantNameMap } from "../core/plantNames";
import { renderPlotJournalPreview } from "../components/journalPreview";
import { confirmDialog, createModal, type ModalOptions } from "../components/dialogCore";
import { selectPlot } from "../components/plotInteractions";
import { getJournalDraftGeneration } from "../services/journalDraft";
import { captureOfflineQueueContext, assertOfflineQueueContext } from "../services/offlineQueue";

let ctx: AppContext;

let issueItems: GardenIssue[] = [];
let issuesTotal = 0;
let issuesOffset = 0;
let issuesLoadSequence = 0;
const ISSUES_PAGE_SIZE = 50;
let gardenGeneration = 0;
const issueDialogs = new Set<() => void>();
export interface IssueFormOpenOptions extends ModalOptions {
  plantIds?: string[];
  plotIds?: string[];
  onSaved?: (issue: GardenIssue) => void;
}

export function setIssuesOffset(offset: number): void {
  issuesOffset = offset;
}

export function resetIssuesForGardenSwitch(): void {
  gardenGeneration += 1;
  for (const close of issueDialogs) close();
  issuesLoadSequence += 1;
  issueItems = [];
  issuesTotal = 0;
  issuesOffset = 0;
  renderIssuesView();
}

export function initIssuesTab(appCtx: AppContext): void {
  ctx = appCtx;

  const addButton = document.getElementById("issues-add-btn");
  if (addButton) {
    addButton.hidden = !ctx.canWrite();
    addButton.addEventListener("click", () => {
      if (!ctx.ensureWriteAccess()) return;
      openIssueForm();
    });
  }
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
  const sequence = ++issuesLoadSequence;
  try {
    await ctx.ensurePlantsCacheLoaded();
    if (sequence !== issuesLoadSequence) return;
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
    if (sequence !== issuesLoadSequence) return;
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
    if (sequence !== issuesLoadSequence) return;
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
    onReopen: (issue) => void handleReopenIssue(issue),
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
  options: IssueFormOpenOptions = {},
): void {
  const readOnly = Boolean(existingIssue) && !ctx.canWrite();
  if (!existingIssue && !ctx.ensureWriteAccess()) return;
  const gardenId = getActiveGardenContext();
  const identity = ctx.getAuthProfile()?.username;
  const generation = gardenGeneration;
  const authGeneration = getJournalDraftGeneration();
  const queueContext = captureOfflineQueueContext();
  const contextCurrent = () => {
    try { assertOfflineQueueContext(queueContext); } catch { return false; }
    return generation === gardenGeneration
    && authGeneration === getJournalDraftGeneration()
    && gardenId === getActiveGardenContext() && identity === ctx.getAuthProfile()?.username;
  };
  let closed = false;
  const { dialog: overlay, close: closeOverlay } = createModal(t("issues.form_title"),
    '<div class="modal-content"></div>', {
      modalParent: options.modalParent,
      onClose: () => {
        closed = true;
        issueDialogs.delete(closeOverlay);
        if (contextCurrent()) options.onClose?.();
      },
    });
  issueDialogs.add(closeOverlay);
  const current = () => !closed && overlay.isConnected && contextCurrent();
  let savedIssueId: string | null = existingIssue?.id ?? null;
  const exposeSavedIssue = async (issueId: string) => {
    try {
      const saved = await fetchIssueApi(issueId);
      if (!current()) return;
      closeOverlay();
      if (options.onSaved) options.onSaved(saved);
      else openIssueForm(saved, { modalParent: options.modalParent });
      void loadIssues();
    } catch (err) {
      if (current()) ctx.showToast(getApiErrorMessage(err), "error");
    }
  };

  const form = createIssueForm({
    issue: existingIssue,
    readOnly,
    availablePlants: ctx.getPlants().map((p) => ({
      plt_id: p.plt_id,
      name: p.name,
    })),
    availablePlots: ctx.getPlots(),
    ...(options.plantIds ? { plantIds: options.plantIds } : {}),
    ...(options.plotIds ? { plotIds: options.plotIds } : {}),
    ...(!existingIssue
        ? {
          onDiagnoseFromPhoto: (context: { plantIds: string[]; plotIds: string[]; symptoms: string },
            apply: (diagnosis: DiagnosisCandidate, photo: File) => void) => {
            if (current()) openIssueDiagnosis(context, apply, current);
          },
        }
      : {}),
    onSave: async (data) => {
      if (!current() || !ctx.ensureWriteAccess()) return;
      try {
        const mediaFiles = ctx.extractPendingMediaFiles(
          data as Record<string, unknown>,
        );
        const issuePayload = ctx.withoutPendingMediaFiles(
          data as Record<string, unknown>,
        );
        if (savedIssueId) {
          await updateIssueApi(
            savedIssueId,
            issuePayload,
          );
        } else if (!ctx.isOnline()) {
          await ctx.enqueueDraft(
            "issue_create",
            data as Record<string, unknown>,
          );
          if (!current()) return;
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
        if (!current()) return;
        if (savedIssueId) {
          try {
            await ctx.uploadTargetMediaFiles(
              "issue",
              savedIssueId,
              mediaFiles,
              { gardenId },
            );
          } catch {
            if (!current()) return;
            ctx.showToast(
              t("media.issue_upload_partial"),
              "error",
            );
            const view = document.createElement("button");
            view.type = "button";
            view.textContent = t("issues.view_saved_issue");
            const issueId = savedIssueId;
            view.addEventListener("click", () => void exposeSavedIssue(issueId));
            form.appendChild(view);
            return;
          }
        }
        if (!current()) return;
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
        if (!existingIssue && savedIssueId) await exposeSavedIssue(savedIssueId);
        else { closeOverlay(); void loadIssues(); }
      } catch (err) {
        if (current()) ctx.showToast(getApiErrorMessage(err), "error");
      }
    },
    onCancel: () => closeOverlay(),
  });
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeOverlay();
  });
  const dialog = overlay.querySelector<HTMLElement>(".modal-content")!;
  dialog.appendChild(form);
  if (existingIssue) {
    ctx.attachReadonlyMediaSection(dialog, {
      targetType: "issue",
      targetId: existingIssue.id,
      emptyText: t("media.issue_empty"),
    });
    attachIssueHistorySection(dialog, existingIssue.id);
  }
  form.querySelector<HTMLElement>("input, select, textarea")?.focus();
}

function openIssueDiagnosis(
  context: { plantIds: string[]; plotIds: string[]; symptoms: string },
  apply: (diagnosis: DiagnosisCandidate, photo: File) => void,
  parentCurrent: () => boolean,
): void {
  let closed = false;
  const { dialog, close } = createModal(t("issues.diagnose_optional"), '<div class="modal-content"></div>', {
    onClose: () => { closed = true; issueDialogs.delete(close); },
  });
  issueDialogs.add(close);
  const current = () => !closed && parentCurrent();
  const content = dialog.querySelector(".modal-content")!;
  const label = document.createElement("label");
  label.textContent = t("diagnose.select_photo");
  const photo = document.createElement("input");
  photo.type = "file";
  photo.accept = AI_PHOTO_UPLOAD_ACCEPT;
  photo.id = `issue-diagnosis-${crypto.randomUUID()}`;
  label.htmlFor = photo.id;
  const diagnose = document.createElement("button");
  diagnose.type = "button";
  diagnose.textContent = t("diagnose.button");
  const results = document.createElement("div");
  results.setAttribute("role", "status");
  diagnose.addEventListener("click", () => {
    const file = photo.files?.[0];
    if (!file || !current()) return;
    const invalid = validateAiPhotoUpload(file);
    if (invalid) {
      results.textContent = t(invalid === "too_large" ? "photo_upload.error_too_large" : "photo_upload.error_unsupported_type");
      return;
    }
    diagnose.disabled = true;
    results.textContent = t("diagnose.loading");
    void diagnosePlantApi({
      image: file,
      ...(context.plantIds.length === 1 ? { pltId: context.plantIds[0]! } : {}),
      ...(context.plotIds.length === 1 ? { plotId: context.plotIds[0]! } : {}),
      ...(context.symptoms ? { symptoms: context.symptoms.slice(0, 500) } : {}),
    }).then((result) => {
      if (!current()) return;
      results.replaceChildren();
      const disclaimer = document.createElement("p");
      disclaimer.textContent = result.disclaimer || t("diagnose.disclaimer");
      results.appendChild(disclaimer);
      if (!result.diagnoses.length) results.append(t("diagnose.no_issues"));
      for (const diagnosis of result.diagnoses) {
        const description = document.createElement("p");
        description.textContent = `${diagnosis.likely_cause}: ${diagnosis.description}`;
        const use = document.createElement("button");
        use.type = "button";
        use.textContent = t("issues.use_diagnosis");
        use.addEventListener("click", () => {
          if (!current()) return;
          apply(diagnosis, file);
          close();
        });
        results.append(description, use);
      }
    }).catch((err: unknown) => {
      if (current()) results.textContent = getApiErrorMessage(err);
    }).finally(() => { if (current()) diagnose.disabled = false; });
  });
  content.append(label, photo, diagnose, results);
  photo.focus();
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
  const generation = gardenGeneration;
  const gardenId = getActiveGardenContext();
  const current = () => dialog.isConnected && generation === gardenGeneration && gardenId === getActiveGardenContext();
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
      if (!current()) return;
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
      if (!current()) return;
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

async function handleReopenIssue(issue: GardenIssue): Promise<void> {
  if (!ctx.ensureWriteAccess()) return;
  try {
    await updateIssueApi(issue.id, { status: "open" });
    ctx.showToast(t("issues.reopened"), "success");
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
