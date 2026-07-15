import type { GardenIssue } from "../core/models";
import { t } from "../core/i18n";
import { createFieldGroup as _createFieldGroup, createParagraph } from "../core/dom";
import { renderPendingMediaPickerLazy } from "./mediaGalleryLoader";
import { createChipInput } from "./chipInput";
import { renderEmptyState } from "./emptyState";

const ISSUE_TYPE_ICONS: Record<string, string> = {
  pest: "\uD83D\uDC1B",
  disease: "\uD83E\uDDA0",
  fungal: "\uD83C\uDF44",
  nutrient: "\uD83E\uDDEA",
  environmental: "\uD83C\uDF21\uFE0F",
  damage: "\uD83D\uDC94",
  other: "\u2753",
};

export interface IssueListCallbacks {
  onEdit: (issue: GardenIssue) => void;
  onResolve: (issue: GardenIssue) => void;
  onReopen: (issue: GardenIssue) => void;
  onDelete: (issue: GardenIssue) => void;
  onPlantClick: (pltId: string) => void;
  onPlotClick: (plotId: string) => void;
  onEmptyAction?: (() => void) | undefined;
  canWrite?: boolean | undefined;
}

export function renderIssueHistoryPreview(
  container: HTMLElement,
  issues: GardenIssue[],
): void {
  container.replaceChildren();
  if (issues.length === 0) {
    const empty = document.createElement("p");
    empty.className = "inventory-summary-empty";
    empty.textContent = t("issues.empty");
    container.appendChild(empty);
    return;
  }

  const heading = document.createElement("div");
  heading.className = "journal-preview-heading";
  heading.textContent = t("issues.title");
  container.appendChild(heading);

  issues.slice(0, 3).forEach((issue) => {
    const row = document.createElement("div");
    row.className = "journal-preview-row";

    const icon = document.createElement("span");
    icon.className = "journal-preview-icon";
    icon.textContent = ISSUE_TYPE_ICONS[issue.issue_type] || "\u2753";

    const text = document.createElement("span");
    text.className = "journal-preview-text";
    const title = issue.title || issue.description || t(`issues.status_${issue.status}`);
    text.textContent = `${t(`issues.type_${issue.issue_type}`)}: ${title}`;

    const date = document.createElement("span");
    date.className = "journal-preview-date";
    date.textContent = new Date(issue.created_at_ms).toLocaleDateString();

    row.append(icon, text, date);
    container.appendChild(row);
  });

  if (issues.length > 3) {
    const more = document.createElement("p");
    more.className = "journal-empty-hint";
    more.textContent = t("inventory.source_more_entries", {
      count: issues.length - 3,
    });
    container.appendChild(more);
  }
}

export function renderIssueList(
  container: HTMLElement,
  issues: GardenIssue[],
  cbs: IssueListCallbacks,
  plantNames?: Map<string, string>,
): void {
  container.replaceChildren();
  if (issues.length === 0) {
    renderEmptyState(container, {
      icon: "\uD83D\uDC1B",
      headline: t("issues.empty"),
      hint: t("issues.empty_hint"),
      ctaLabel: cbs.onEmptyAction ? t("issues.empty_cta") : undefined,
      ctaAction: cbs.onEmptyAction,
    });
    return;
  }
  for (const issue of issues) {
    container.appendChild(createIssueCard(issue, cbs, plantNames));
  }
}

function createIssueCard(
  issue: GardenIssue,
  cbs: IssueListCallbacks,
  plantNames?: Map<string, string>,
): HTMLElement {
  const card = document.createElement("div");
  card.className = `issue-card${issue.status === "resolved" ? " issue-resolved" : ""}${issue.severity === "critical" ? " severity-critical" : ""}${issue.severity === "high" ? " severity-high" : ""}`;

  // Header: icon + type label
  const header = document.createElement("div");
  header.className = "issue-card-header";
  const icon = document.createElement("span");
  icon.className = "issue-card-icon";
  icon.textContent = ISSUE_TYPE_ICONS[issue.issue_type] || "\u2753";
  const typeLabel = document.createElement("span");
  typeLabel.className = "issue-card-type";
  typeLabel.textContent = t(`issues.type_${issue.issue_type}`);
  const severityChip = document.createElement("span");
  severityChip.className = `issue-severity-chip severity-${issue.severity}`;
  severityChip.textContent = t(`issues.severity_${issue.severity}`);
  const statusChip = document.createElement("span");
  statusChip.className = `issue-status-chip status-${issue.status}`;
  statusChip.textContent = t(`issues.status_${issue.status}`);
  header.append(icon, typeLabel, severityChip, statusChip);
  card.appendChild(header);

  // Title
  if (issue.title) {
    const titleEl = document.createElement("div");
    titleEl.className = "issue-card-title";
    titleEl.textContent = issue.title;
    card.appendChild(titleEl);
  }

  // Description excerpt
  if (issue.description) {
    const desc = document.createElement("div");
    desc.className = "issue-card-description";
    desc.textContent = issue.description;
    card.appendChild(desc);
  }

  // Meta: suspected cause, treatment plan
  const meta = document.createElement("div");
  meta.className = "issue-card-meta";
  let hasMeta = false;
  if (issue.suspected_cause) {
    const causeSpan = document.createElement("span");
    causeSpan.append(
      createMetaLabel(t("issues.form_cause")),
      document.createTextNode(` ${issue.suspected_cause}`),
    );
    meta.appendChild(causeSpan);
    hasMeta = true;
  }
  if (issue.treatment_plan) {
    const treatSpan = document.createElement("span");
    treatSpan.append(
      createMetaLabel(t("issues.form_treatment")),
      document.createTextNode(` ${issue.treatment_plan}`),
    );
    meta.appendChild(treatSpan);
    hasMeta = true;
  }
  if (hasMeta) card.appendChild(meta);

  // Follow-up date
  if (issue.follow_up_on) {
    const followUp = document.createElement("div");
    followUp.className = "issue-card-follow-up";
    const now = new Date().toISOString().slice(0, 10);
    const isOverdue = issue.follow_up_on < now && issue.status !== "resolved" && issue.status !== "dismissed";
    if (isOverdue) {
      followUp.classList.add("overdue");
      followUp.textContent = `${t("issues.follow_up_overdue")}: ${issue.follow_up_on}`;
    } else {
      followUp.textContent = `${t("issues.form_follow_up")}: ${issue.follow_up_on}`;
    }
    card.appendChild(followUp);
  }

  // Linked plant/plot tags
  if (issue.plant_ids.length > 0 || issue.plot_ids.length > 0) {
    const tags = document.createElement("div");
    tags.className = "issue-card-tags";
    for (const pltId of issue.plant_ids) {
      const tag = document.createElement("button");
      tag.type = "button";
      tag.className = "journal-tag journal-tag-plant";
      tag.textContent = plantNames?.get(pltId) ?? pltId;
      tag.addEventListener("click", (e) => {
        e.stopPropagation();
        cbs.onPlantClick(pltId);
      });
      tags.appendChild(tag);
    }
    for (const plotId of issue.plot_ids) {
      const tag = document.createElement("button");
      tag.type = "button";
      tag.className = "journal-tag journal-tag-plot";
      tag.textContent = plotId;
      tag.addEventListener("click", (e) => {
        e.stopPropagation();
        cbs.onPlotClick(plotId);
      });
      tags.appendChild(tag);
    }
    card.appendChild(tags);
  }

  // Footer: timestamp + actions
  const footer = document.createElement("div");
  footer.className = "issue-card-footer";
  const timestamp = document.createElement("span");
  timestamp.textContent = new Date(issue.created_at_ms).toLocaleDateString();
  footer.appendChild(timestamp);

  const actions = document.createElement("div");
  actions.className = "issue-card-actions";

  const detailsBtn = document.createElement("button");
  detailsBtn.type = "button";
  detailsBtn.className = "issue-action-btn issue-action-details";
  detailsBtn.textContent = t(
    cbs.canWrite === false
      ? "issues.action_view_details"
      : "common.settings",
  );
  detailsBtn.addEventListener("click", () => cbs.onEdit(issue));
  actions.appendChild(detailsBtn);

  if (cbs.canWrite !== false) {

    if (issue.status !== "resolved" && issue.status !== "dismissed") {
      const resolveBtn = document.createElement("button");
      resolveBtn.type = "button";
      resolveBtn.className = "issue-action-btn issue-action-resolve";
      resolveBtn.textContent = t("issues.action_resolve");
      resolveBtn.addEventListener("click", () => cbs.onResolve(issue));
      actions.appendChild(resolveBtn);
    } else {
      const reopenBtn = document.createElement("button");
      reopenBtn.type = "button";
      reopenBtn.className = "issue-action-btn issue-action-reopen";
      reopenBtn.textContent = t("issues.action_reopen");
      reopenBtn.addEventListener("click", () => cbs.onReopen(issue));
      actions.appendChild(reopenBtn);
    }

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "issue-action-btn issue-action-delete";
    deleteBtn.textContent = t("common.delete");
    deleteBtn.addEventListener("click", () => cbs.onDelete(issue));
    actions.appendChild(deleteBtn);
  }
  footer.appendChild(actions);
  card.appendChild(footer);

  return card;
}

export function createIssueForm(options: {
  issue?: GardenIssue | undefined;
  readOnly?: boolean | undefined;
  availablePlants?: Array<{ plt_id: string; name: string }>;
  availablePlots?: string[];
  onSave: (data: Record<string, unknown>) => Promise<void>;
  onCancel: () => void;
  onDiagnoseFromPhoto?: () => void;
}): HTMLElement {
  const {
    issue,
    readOnly = false,
    availablePlants,
    availablePlots,
    onSave,
    onCancel,
  } = options;
  const form = document.createElement("form");
  form.className = "modal-form";

  const heading = document.createElement("h3");
  heading.textContent = t("issues.form_title");
  form.appendChild(heading);

  // Diagnose from photo button (only for new issues)
  if (!issue && options.onDiagnoseFromPhoto) {
    const diagBtn = document.createElement("button");
    diagBtn.type = "button";
    diagBtn.className = "btn-secondary";
    diagBtn.style.marginBottom = "var(--sp-2)";
    diagBtn.style.width = "100%";
    diagBtn.textContent = t("diagnose.title");
    const cb = options.onDiagnoseFromPhoto;
    diagBtn.addEventListener("click", () => cb());
    form.appendChild(diagBtn);
  }

  // Issue type
  const typeGroup = createFieldGroup(t("issues.form_type"));
  const typeSelect = document.createElement("select");
  typeSelect.name = "issue_type";
  const types = ["pest", "disease", "fungal", "nutrient", "environmental", "damage", "other"];
  for (const tp of types) {
    const opt = document.createElement("option");
    opt.value = tp;
    opt.textContent = t(`issues.type_${tp}`);
    if (issue?.issue_type === tp) opt.selected = true;
    typeSelect.appendChild(opt);
  }
  typeGroup.appendChild(typeSelect);
  form.appendChild(typeGroup);

  // Title
  const titleGroup = createFieldGroup(t("issues.form_name"));
  const titleInput = document.createElement("input");
  titleInput.type = "text";
  titleInput.name = "title";
  titleInput.maxLength = 200;
  titleInput.value = issue?.title || "";
  titleGroup.appendChild(titleInput);
  form.appendChild(titleGroup);

  // Description
  const descGroup = createFieldGroup(t("issues.form_description"));
  const descArea = document.createElement("textarea");
  descArea.name = "description";
  descArea.maxLength = 4000;
  descArea.rows = 3;
  descArea.value = issue?.description || "";
  descGroup.appendChild(descArea);
  form.appendChild(descGroup);

  // Severity
  const sevGroup = createFieldGroup(t("issues.form_severity"));
  const sevSelect = document.createElement("select");
  sevSelect.name = "severity";
  const sevs = ["low", "normal", "high", "critical"];
  for (const sv of sevs) {
    const opt = document.createElement("option");
    opt.value = sv;
    opt.textContent = t(`issues.severity_${sv}`);
    if ((issue?.severity || "normal") === sv) opt.selected = true;
    sevSelect.appendChild(opt);
  }
  sevGroup.appendChild(sevSelect);
  form.appendChild(sevGroup);

  // Suspected cause
  const causeGroup = createFieldGroup(t("issues.form_cause"));
  const causeArea = document.createElement("textarea");
  causeArea.name = "suspected_cause";
  causeArea.maxLength = 1000;
  causeArea.rows = 2;
  causeArea.value = issue?.suspected_cause || "";
  causeGroup.appendChild(causeArea);
  form.appendChild(causeGroup);

  // Treatment plan
  const treatGroup = createFieldGroup(t("issues.form_treatment"));
  const treatArea = document.createElement("textarea");
  treatArea.name = "treatment_plan";
  treatArea.maxLength = 2000;
  treatArea.rows = 2;
  treatArea.value = issue?.treatment_plan || "";
  treatGroup.appendChild(treatArea);
  form.appendChild(treatGroup);

  // Follow-up date
  const fuGroup = createFieldGroup(t("issues.form_follow_up"));
  const fuInput = document.createElement("input");
  fuInput.type = "date";
  fuInput.name = "follow_up_on";
  fuInput.value = issue?.follow_up_on || "";
  fuGroup.appendChild(fuInput);
  form.appendChild(fuGroup);

  // Plant IDs (chip input)
  const plantChipInput = createChipInput({
    label: t("issues.form_plant_ids"),
    placeholder: t("issues.form_plant_ids_placeholder"),
    items: availablePlants ?? [],
    getKey: (p) => p.plt_id,
    getLabel: (p) => `${p.name} (${p.plt_id})`,
    getSearchText: (p) => `${p.plt_id} ${p.name}`.toLowerCase(),
    selected: issue?.plant_ids ?? [],
  });
  form.appendChild(plantChipInput.container);

  // Plot IDs (chip input)
  const plotChipInput = createChipInput({
    label: t("issues.form_plot_ids"),
    placeholder: t("issues.form_plot_ids_placeholder"),
    items: (availablePlots ?? []).map((id) => ({ id })),
    getKey: (p) => p.id,
    getLabel: (p) => p.id,
    selected: issue?.plot_ids ?? [],
  });
  form.appendChild(plotChipInput.container);

  const pendingFiles: File[] = [];
  const mediaGroup = createFieldGroup(t("media.attach_photos_optional"));
  const mediaPicker = document.createElement("div");
  const renderMedia = (progressPct: number | null = null) => {
    void renderPendingMediaPickerLazy(mediaPicker, {
      files: pendingFiles,
      emptyText: t("media.pending_empty"),
      uploadProgressPct: progressPct,
      onFilesSelected: (files) => {
        pendingFiles.push(...files);
        renderMedia();
      },
      onRemoveFile: (index) => {
        pendingFiles.splice(index, 1);
        renderMedia();
      },
    });
  };
  renderMedia();
  mediaGroup.appendChild(mediaPicker);
  form.appendChild(mediaGroup);

  // Buttons
  const btnRow = document.createElement("div");
  btnRow.className = "modal-form-actions";
  const saveBtn = document.createElement("button");
  saveBtn.type = "submit";
  saveBtn.className = "btn-primary";
  saveBtn.textContent = t("common.save");
  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.textContent = t("common.close");
  cancelBtn.addEventListener("click", onCancel);
  btnRow.append(cancelBtn, saveBtn);
  form.appendChild(btnRow);

  if (readOnly) {
    saveBtn.hidden = true;
    form
      .querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement | HTMLButtonElement>(
        "input, textarea, select, button",
      )
      .forEach((control) => {
        if (control !== cancelBtn) {
          control.disabled = true;
        }
      });
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const data: Record<string, unknown> = {
      issue_type: typeSelect.value,
      title: titleInput.value,
      description: descArea.value,
      severity: sevSelect.value,
      suspected_cause: causeArea.value,
      treatment_plan: treatArea.value,
      follow_up_on: fuInput.value || undefined,
      plant_ids: plantChipInput.getSelectedKeys(),
      plot_ids: plotChipInput.getSelectedKeys(),
      media_files: [...pendingFiles],
    };
    void onSave(data);
  });

  return form;
}

function createFieldGroup(label: string): HTMLElement {
  return _createFieldGroup(label, "modal-field-group", "modal-field-label");
}

function createMetaLabel(text: string): HTMLSpanElement {
  const label = document.createElement("span");
  label.className = "issue-card-meta-label";
  label.textContent = `${text}:`;
  return label;
}
