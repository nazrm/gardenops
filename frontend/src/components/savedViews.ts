import type { SavedView, SavedViewPreset } from "../core/models";
import { t } from "../core/i18n";
import { clearChildren } from "../core/sanitize";

export interface SavedViewsCallbacks {
  onApply: (view: SavedView | SavedViewPreset) => void;
  onSave: (viewType: string, label: string, filters: Record<string, unknown>) => void;
  onDelete: (view: SavedView) => void;
}

export function renderSavedViewsDropdown(
  container: HTMLElement,
  views: SavedView[],
  presets: SavedViewPreset[],
  activeViewType: string,
  cbs: SavedViewsCallbacks,
): void {
  clearChildren(container);

  const filteredPresets = presets.filter((p) => p.view_type === activeViewType);
  const filteredViews = views.filter((v) => v.view_type === activeViewType);

  // Presets section
  if (filteredPresets.length > 0) {
    const section = document.createElement("div");
    section.className = "saved-views-section";
    const title = document.createElement("div");
    title.className = "saved-views-section-title";
    title.textContent = t("saved_views.presets");
    section.appendChild(title);

    for (const preset of filteredPresets) {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "saved-views-item";
      const label = document.createElement("span");
      label.className = "saved-views-item-label";
      label.textContent = preset.label;
      item.appendChild(label);
      item.addEventListener("click", () => cbs.onApply(preset));
      section.appendChild(item);
    }

    container.appendChild(section);
  }

  // Divider
  if (filteredPresets.length > 0) {
    const hr = document.createElement("hr");
    hr.className = "saved-views-divider";
    container.appendChild(hr);
  }

  // User saved views section
  const userSection = document.createElement("div");
  userSection.className = "saved-views-section";
  const userTitle = document.createElement("div");
  userTitle.className = "saved-views-section-title";
  userTitle.textContent = t("saved_views.my_views");
  userSection.appendChild(userTitle);

  if (filteredViews.length === 0) {
    const empty = document.createElement("div");
    empty.className = "saved-views-empty";
    empty.textContent = t("saved_views.empty");
    userSection.appendChild(empty);
  } else {
    for (const view of filteredViews) {
      const item = document.createElement("div");
      item.className = "saved-views-item";

      const labelSpan = document.createElement("button");
      labelSpan.type = "button";
      labelSpan.className = "saved-views-item-label";
      labelSpan.style.background = "none";
      labelSpan.style.border = "none";
      labelSpan.style.padding = "0";
      labelSpan.style.cursor = "pointer";
      labelSpan.style.textAlign = "left";
      labelSpan.style.color = "inherit";
      labelSpan.style.font = "inherit";
      labelSpan.textContent = view.label;
      labelSpan.addEventListener("click", () => cbs.onApply(view));

      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className = "saved-views-item-delete";
      deleteBtn.textContent = "\u00d7";
      deleteBtn.title = t("common.delete");
      deleteBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        cbs.onDelete(view);
      });

      item.appendChild(labelSpan);
      item.appendChild(deleteBtn);
      userSection.appendChild(item);
    }
  }

  container.appendChild(userSection);

  // Save current filters button
  const hr2 = document.createElement("hr");
  hr2.className = "saved-views-divider";
  container.appendChild(hr2);

  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "saved-views-save-btn";
  saveBtn.textContent = t("saved_views.save_current");
  saveBtn.addEventListener("click", () => {
    const name = prompt(t("saved_views.save_prompt"));
    if (name && name.trim()) {
      cbs.onSave(activeViewType, name.trim(), {});
    }
  });
  container.appendChild(saveBtn);
}

export function createSaveViewDialog(
  viewType: string,
  currentFilters: Record<string, unknown>,
  onSave: (label: string) => void,
  onCancel: () => void,
): HTMLElement {
  const el = document.createElement("div");
  el.className = "saved-views-save-dialog";
  const label = document.createElement("label");
  label.textContent = t("saved_views.save_prompt");

  const input = document.createElement("input");
  input.type = "text";
  input.className = "saved-views-name-input";
  input.maxLength = 100;

  const actions = document.createElement("div");
  actions.className = "saved-views-dialog-actions";

  const cancelButton = document.createElement("button");
  cancelButton.type = "button";
  cancelButton.className = "saved-views-dialog-cancel";
  cancelButton.textContent = t("common.close");
  cancelButton.addEventListener("click", onCancel);

  const saveButton = document.createElement("button");
  saveButton.type = "button";
  saveButton.className = "saved-views-dialog-save";
  saveButton.textContent = t("common.save");
  saveButton.addEventListener("click", () => {
    const val = input?.value.trim();
    if (val) onSave(val);
  });

  actions.append(cancelButton, saveButton);
  el.append(label, input, actions);
  return el;
}
