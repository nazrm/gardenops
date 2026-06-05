import type { JournalEntry, JournalEventType } from "../core/models";
import { formatPlotLabel } from "../core/models";
import { t } from "../core/i18n";
import { createFieldGroup as _createFieldGroup } from "../core/dom";
import type { MediaAsset } from "../services/api";
import {
  createLazyMediaThumbnailButton,
  renderPendingMediaPickerLazy,
} from "./mediaGalleryLoader";
import { renderEmptyState } from "./emptyState";

import {
  formatJournalDate,
  JOURNAL_EVENT_ICONS,
  journalEventLabel,
} from "./journalPreview";

export { journalEventLabel } from "./journalPreview";

const JOURNAL_EVENT_TYPES: JournalEventType[] = [
  "planted",
  "moved",
  "divided",
  "pruned",
  "watered",
  "fertilized",
  "bloomed",
  "harvested",
  "died",
  "observed",
];

function relativeTime(ms: number): string {
  const now = Date.now();
  const diff = now - ms;
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return t("journal.relative_just_now");
  if (minutes < 60) return t("journal.relative_minutes", { count: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t("journal.relative_hours", { count: hours });
  const days = Math.floor(hours / 24);
  if (days < 7) return t("journal.relative_days", { count: days });
  return formatJournalDate(new Date(ms).toISOString().slice(0, 10));
}

export interface JournalListCallbacks {
  onEdit: (entry: JournalEntry) => void;
  onDelete: (entry: JournalEntry) => void;
  onPlantClick: (pltId: string) => void;
  onPlotClick: (plotId: string) => void;
  mediaPreviewByEntryId?: ReadonlyMap<string, MediaAsset | null> | undefined;
  onEmptyAction?: (() => void) | undefined;
  canWrite?: boolean | undefined;
}

export function renderJournalList(
  container: HTMLElement,
  entries: JournalEntry[],
  cbs: JournalListCallbacks,
  plantNames?: Map<string, string>,
): void {
  container.replaceChildren();
  if (entries.length === 0) {
    renderEmptyState(container, {
      icon: "\uD83D\uDCD6",
      headline: t("journal.empty"),
      hint: t("journal.empty_hint"),
      ctaLabel: cbs.onEmptyAction ? t("journal.empty_cta") : undefined,
      ctaAction: cbs.onEmptyAction,
    });
    return;
  }

  for (const entry of entries) {
    const card = createJournalCard(entry, cbs, plantNames);
    container.appendChild(card);
  }
}

function createJournalCard(
  entry: JournalEntry,
  cbs: JournalListCallbacks,
  plantNames?: Map<string, string>,
): HTMLElement {
  const card = document.createElement("div");
  card.className = "journal-card";
  card.dataset["entryId"] = String(entry.id);

  const header = document.createElement("div");
  header.className = "journal-card-header";

  const iconSpan = document.createElement("span");
  iconSpan.className = "journal-card-icon";
  iconSpan.textContent = JOURNAL_EVENT_ICONS[entry.event_type] ?? "";

  const typeLabel = document.createElement("span");
  typeLabel.className = "journal-card-type";
  typeLabel.textContent = journalEventLabel(entry.event_type);

  const dateSpan = document.createElement("span");
  dateSpan.className = "journal-card-date";
  dateSpan.textContent = formatJournalDate(entry.occurred_on);

  header.append(iconSpan, typeLabel, dateSpan);

  if (entry.title) {
    const title = document.createElement("div");
    title.className = "journal-card-title";
    title.textContent = entry.title;
    card.append(header, title);
  } else {
    card.appendChild(header);
  }

  if (entry.notes) {
    const notes = document.createElement("div");
    notes.className = "journal-card-notes";
    notes.textContent =
      entry.notes.length > 120
        ? entry.notes.slice(0, 120) + "\u2026"
        : entry.notes;
    card.appendChild(notes);
  }

  const previewAsset = cbs.mediaPreviewByEntryId?.get(String(entry.id)) ?? null;
  if (previewAsset) {
    const thumbRow = document.createElement("div");
    thumbRow.className = "journal-card-media";
    thumbRow.appendChild(createLazyMediaThumbnailButton(previewAsset, {
      className: "journal-card-thumb",
      imageClassName: "journal-card-thumb-image",
      label: previewAsset.original_filename || t("media.latest_photo"),
    }));
    card.appendChild(thumbRow);
  }

  const tags = document.createElement("div");
  tags.className = "journal-card-tags";

  for (const pltId of entry.plant_ids) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "journal-tag journal-tag-plant";
    chip.textContent = plantNames?.get(pltId) ?? pltId;
    chip.addEventListener("click", (e) => {
      e.stopPropagation();
      cbs.onPlantClick(pltId);
    });
    tags.appendChild(chip);
  }
  const plotList = entry.plots ?? entry.plot_ids.map((id: string) => ({ plot_id: id, zone_name: "" }));
  for (const plot of plotList) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "journal-tag journal-tag-plot";
    chip.textContent = formatPlotLabel(plot.plot_id, plot.zone_name);
    chip.addEventListener("click", (e) => {
      e.stopPropagation();
      cbs.onPlotClick(plot.plot_id);
    });
    tags.appendChild(chip);
  }
  if (tags.childElementCount > 0) {
    card.appendChild(tags);
  }

  const footer = document.createElement("div");
  footer.className = "journal-card-footer";

  if (entry.actor_username) {
    const actor = document.createElement("span");
    actor.className = "journal-card-actor";
    actor.textContent = entry.actor_username;
    footer.appendChild(actor);
  }

  const time = document.createElement("span");
  time.className = "journal-card-time";
  time.textContent = relativeTime(entry.created_at_ms);
  footer.appendChild(time);

  if (cbs.canWrite !== false) {
    const actions = document.createElement("div");
    actions.className = "journal-card-actions";

    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "journal-action-btn";
    editBtn.textContent = t("common.edit");
    editBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      cbs.onEdit(entry);
    });

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "journal-action-btn journal-action-delete";
    deleteBtn.textContent = t("common.delete");
    deleteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      cbs.onDelete(entry);
    });

    actions.append(editBtn, deleteBtn);
    footer.appendChild(actions);
  }
  card.appendChild(footer);

  return card;
}

export interface JournalComposerOptions {
  plantIds?: string[];
  plotIds?: string[];
  prefillEventType?: JournalEventType;
  availablePlants: Array<{ plt_id: string; name: string }>;
  availablePlots: string[];
  onSubmit: (data: {
    event_type: JournalEventType;
    occurred_on: string;
    title: string;
    notes: string;
    plant_ids: string[];
    plot_ids: string[];
    media_files: File[];
  }, controls: {
    setUploadProgress: (pct: number | null) => void;
    setBusy: (busy: boolean) => void;
  }) => void | Promise<void>;
  onCancel: () => void;
  editEntry?: JournalEntry | undefined;
}

export function createJournalComposerEl(
  opts: JournalComposerOptions,
): HTMLElement {
  const form = document.createElement("form");
  form.className = "journal-composer";
  form.addEventListener("submit", (e) => e.preventDefault());

  const isEdit = !!opts.editEntry;
  const heading = document.createElement("h3");
  heading.className = "journal-composer-heading";
  heading.textContent = isEdit ? t("journal.edit_heading") : t("journal.new_heading");
  form.appendChild(heading);

  // Event type
  const typeGroup = createFieldGroup(t("journal.field_event_type"));
  const typeSelect = document.createElement("select");
  typeSelect.name = "event_type";
  typeSelect.required = true;
  for (const value of JOURNAL_EVENT_TYPES) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = `${JOURNAL_EVENT_ICONS[value]} ${journalEventLabel(value)}`;
    typeSelect.appendChild(opt);
  }
  typeSelect.value = opts.editEntry?.event_type ?? opts.prefillEventType ?? "observed";
  typeGroup.appendChild(typeSelect);
  form.appendChild(typeGroup);

  // Date
  const dateGroup = createFieldGroup(t("journal.field_date"));
  const dateInput = document.createElement("input");
  dateInput.type = "date";
  dateInput.name = "occurred_on";
  dateInput.required = true;
  dateInput.value = opts.editEntry?.occurred_on ?? new Date().toISOString().slice(0, 10);
  dateGroup.appendChild(dateInput);
  form.appendChild(dateGroup);

  // Title
  const titleGroup = createFieldGroup(t("journal.field_title_optional"));
  const titleInput = document.createElement("input");
  titleInput.type = "text";
  titleInput.name = "title";
  titleInput.maxLength = 200;
  titleInput.placeholder = t("journal.title_placeholder");
  titleInput.value = opts.editEntry?.title ?? "";
  titleGroup.appendChild(titleInput);
  form.appendChild(titleGroup);

  // Notes
  const notesGroup = createFieldGroup(t("journal.field_notes_optional"));
  const notesArea = document.createElement("textarea");
  notesArea.name = "notes";
  notesArea.maxLength = 4000;
  notesArea.rows = 3;
  notesArea.placeholder = t("journal.notes_placeholder");
  notesArea.value = opts.editEntry?.notes ?? "";
  notesGroup.appendChild(notesArea);
  form.appendChild(notesGroup);

  // Plant selection
  const plantGroup = createFieldGroup(t("journal.field_plants"));
  const selectedPlants = new Set<string>(
    opts.editEntry?.plant_ids ?? opts.plantIds ?? [],
  );
  const plantChips = document.createElement("div");
  plantChips.className = "journal-chip-list";
  const plantSelect = document.createElement("select");
  plantSelect.className = "journal-add-select";
  const defaultOpt = document.createElement("option");
  defaultOpt.value = "";
  defaultOpt.textContent = t("journal.add_plant");
  plantSelect.appendChild(defaultOpt);
  for (const p of opts.availablePlants) {
    const opt = document.createElement("option");
    opt.value = p.plt_id;
    opt.textContent = p.name;
    plantSelect.appendChild(opt);
  }
  plantSelect.addEventListener("change", () => {
    if (plantSelect.value) {
      selectedPlants.add(plantSelect.value);
      renderChips(plantChips, selectedPlants, (id) => {
        selectedPlants.delete(id);
        renderChips(plantChips, selectedPlants, () => {});
      });
      plantSelect.value = "";
    }
  });
  renderChips(plantChips, selectedPlants, (id) => {
    selectedPlants.delete(id);
    renderChips(plantChips, selectedPlants, (id2) => {
      selectedPlants.delete(id2);
      renderChips(plantChips, selectedPlants, () => {});
    });
  });
  plantGroup.append(plantChips, plantSelect);
  form.appendChild(plantGroup);

  // Plot selection
  const plotGroup = createFieldGroup(t("journal.field_plots"));
  const selectedPlots = new Set<string>(
    opts.editEntry?.plot_ids ?? opts.plotIds ?? [],
  );
  const plotChips = document.createElement("div");
  plotChips.className = "journal-chip-list";
  const plotSelect = document.createElement("select");
  plotSelect.className = "journal-add-select";
  const plotDefaultOpt = document.createElement("option");
  plotDefaultOpt.value = "";
  plotDefaultOpt.textContent = t("journal.add_plot");
  plotSelect.appendChild(plotDefaultOpt);
  for (const plotId of opts.availablePlots) {
    const opt = document.createElement("option");
    opt.value = plotId;
    opt.textContent = plotId;
    plotSelect.appendChild(opt);
  }
  plotSelect.addEventListener("change", () => {
    if (plotSelect.value) {
      selectedPlots.add(plotSelect.value);
      refreshPlotChips();
      plotSelect.value = "";
    }
  });

  function refreshPlotChips(): void {
    renderChips(plotChips, selectedPlots, (id) => {
      selectedPlots.delete(id);
      refreshPlotChips();
    });
  }
  refreshPlotChips();

  plotGroup.append(plotChips, plotSelect);
  form.appendChild(plotGroup);

  const mediaGroup = createFieldGroup(t("media.attach_photos_optional"));
  const mediaPicker = document.createElement("div");
  let pendingFiles: File[] = [];
  let uploadProgressPct: number | null = null;
  const rerenderPendingMedia = () => {
    void renderPendingMediaPickerLazy(mediaPicker, {
      files: pendingFiles,
      emptyText: t("media.pending_empty"),
      uploadProgressPct,
      onFilesSelected: (files) => {
        pendingFiles = pendingFiles.concat(files);
        rerenderPendingMedia();
      },
      onRemoveFile: (index) => {
        pendingFiles = pendingFiles.filter((_, candidate) => candidate !== index);
        rerenderPendingMedia();
      },
    });
  };
  rerenderPendingMedia();
  mediaGroup.appendChild(mediaPicker);
  form.appendChild(mediaGroup);

  // Buttons
  const btnRow = document.createElement("div");
  btnRow.className = "journal-composer-buttons";

  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "journal-btn-cancel";
  cancelBtn.textContent = t("common.cancel");
  cancelBtn.addEventListener("click", opts.onCancel);

  const submitBtn = document.createElement("button");
  submitBtn.type = "submit";
  submitBtn.className = "btn-primary journal-btn-submit";
  submitBtn.textContent = isEdit ? t("common.save") : t("journal.add_entry");
  let busy = false;
  const setBusy = (value: boolean) => {
    busy = value;
    submitBtn.disabled = value;
    cancelBtn.disabled = value;
    typeSelect.disabled = value;
    dateInput.disabled = value;
    titleInput.disabled = value;
    notesArea.disabled = value;
    plantSelect.disabled = value;
    plotSelect.disabled = value;
  };
  const setUploadProgress = (pct: number | null) => {
    uploadProgressPct = pct;
    rerenderPendingMedia();
  };
  submitBtn.addEventListener("click", async () => {
    if (busy) return;
    const eventType = typeSelect.value as JournalEventType;
    const occurredOn = dateInput.value;
    if (!occurredOn) return;
    setBusy(true);
    try {
      await opts.onSubmit({
        event_type: eventType,
        occurred_on: occurredOn,
        title: titleInput.value.trim(),
        notes: notesArea.value.trim(),
        plant_ids: [...selectedPlants],
        plot_ids: [...selectedPlots],
        media_files: [...pendingFiles],
      }, {
        setUploadProgress,
        setBusy,
      });
    } finally {
      setBusy(false);
      setUploadProgress(null);
    }
  });

  btnRow.append(cancelBtn, submitBtn);
  form.appendChild(btnRow);

  return form;
}

function createFieldGroup(label: string): HTMLElement {
  return _createFieldGroup(label, "journal-field-group", "journal-field-label");
}

function renderChips(
  container: HTMLElement,
  items: Set<string>,
  onRemove: (id: string) => void,
): void {
  container.replaceChildren();
  for (const id of items) {
    const chip = document.createElement("span");
    chip.className = "journal-chip";
    chip.textContent = id;
    const x = document.createElement("button");
    x.type = "button";
    x.className = "journal-chip-remove";
    x.textContent = "\u00d7";
    x.setAttribute("aria-label", t("journal.remove_chip", { id }));
    x.addEventListener("click", () => onRemove(id));
    chip.appendChild(x);
    container.appendChild(chip);
  }
}
