import { t } from "../core/i18n";
import { createModal } from "./dialogCore";
import {
  AI_PHOTO_UPLOAD_ACCEPT,
  getApiErrorMessage,
  identifyPlantApi,
  validateAiPhotoUpload,
} from "../services/api";
import type { PlantCandidate } from "../services/api";
import { clearChildren } from "../core/sanitize";

export interface IdentifyPlantCallbacks {
  onAddPlant: (prefill: { name: string; latin: string; category: string }) => void;
  onClose: () => void;
}

const ORGAN_OPTIONS: ReadonlyArray<{ value: string; labelKey: string }> = [
  { value: "auto", labelKey: "identify.organ_auto" },
  { value: "flower", labelKey: "identify.organ_flower" },
  { value: "leaf", labelKey: "identify.organ_leaf" },
  { value: "fruit", labelKey: "identify.organ_fruit" },
  { value: "bark", labelKey: "identify.organ_bark" },
  { value: "habit", labelKey: "identify.organ_habit" },
];

export function showIdentifyPlantModal(cbs: IdentifyPlantCallbacks): void {
  const { dialog, close } = createModal(
    t("identify.title"),
    `<div class="modal-content identify-modal">
      <h2 class="modal-title">${t("identify.title")}</h2>
      <div id="identify-body"></div>
    </div>`,
  );

  const body = dialog.querySelector<HTMLElement>("#identify-body")!;
  let selectedFile: File | null = null;
  let previewUrl: string | null = null;

  function cleanup(): void {
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      previewUrl = null;
    }
    close();
    cbs.onClose();
  }

  function renderForm(): void {
    clearChildren(body);

    // File input
    const fileLabel = document.createElement("label");
    fileLabel.className = "modal-field-label";
    fileLabel.textContent = t("identify.select_photo");
    body.appendChild(fileLabel);

    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = AI_PHOTO_UPLOAD_ACCEPT;
    fileInput.setAttribute("capture", "environment");
    fileInput.className = "modal-file-input";
    body.appendChild(fileInput);

    // Preview
    const preview = document.createElement("img");
    preview.className = "identify-photo-preview";
    preview.style.display = "none";
    preview.alt = "";
    body.appendChild(preview);

    // Organ selector
    const organLabel = document.createElement("label");
    organLabel.className = "modal-field-label";
    organLabel.textContent = t("identify.organ_label");
    organLabel.style.marginTop = "var(--sp-2)";
    body.appendChild(organLabel);

    const organSelect = document.createElement("select");
    organSelect.className = "organ-select";
    for (const opt of ORGAN_OPTIONS) {
      const option = document.createElement("option");
      option.value = opt.value;
      option.textContent = t(opt.labelKey);
      organSelect.appendChild(option);
    }
    body.appendChild(organSelect);

    // Actions
    const actions = document.createElement("div");
    actions.className = "modal-form-actions";

    const identifyBtn = document.createElement("button");
    identifyBtn.type = "button";
    identifyBtn.className = "btn-primary";
    identifyBtn.textContent = t("identify.button");
    identifyBtn.disabled = true;

    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "btn-secondary";
    cancelBtn.textContent = t("common.close");

    actions.append(identifyBtn, cancelBtn);
    body.appendChild(actions);

    // Events
    fileInput.addEventListener("change", () => {
      const file = fileInput.files?.[0];
      if (!file) return;
      const validationMessage = validationMessageForFile(file);
      if (validationMessage) {
        selectedFile = null;
        identifyBtn.disabled = true;
        if (previewUrl) {
          URL.revokeObjectURL(previewUrl);
          previewUrl = null;
        }
        preview.removeAttribute("src");
        preview.style.display = "none";
        renderError(validationMessage);
        return;
      }
      selectedFile = file;
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(file);
      preview.src = previewUrl;
      preview.style.display = "block";
      identifyBtn.disabled = false;
    });

    cancelBtn.addEventListener("click", cleanup);

    identifyBtn.addEventListener("click", () => {
      if (!selectedFile) return;
      void runIdentification(selectedFile, organSelect.value);
    });
  }

  async function runIdentification(file: File, organ: string): Promise<void> {
    const validationMessage = validationMessageForFile(file);
    if (validationMessage) {
      renderError(validationMessage);
      return;
    }
    clearChildren(body);
    const loader = document.createElement("div");
    loader.className = "identify-loading";
    loader.textContent = t("identify.loading");
    body.appendChild(loader);

    try {
      const result = await identifyPlantApi({ image: file, organ });
      renderResults(result.candidates, result.attribution);
    } catch (err) {
      renderError(getApiErrorMessage(err));
    }
  }

  function renderResults(candidates: PlantCandidate[], attribution: string): void {
    clearChildren(body);

    if (candidates.length === 0) {
      const empty = document.createElement("p");
      empty.className = "identify-empty";
      empty.textContent = t("identify.no_results");
      body.appendChild(empty);
    } else {
      const list = document.createElement("div");
      list.className = "candidate-list";

      for (const c of candidates) {
        const card = document.createElement("div");
        card.className = "candidate-card";

        // Name
        const nameEl = document.createElement("div");
        nameEl.className = "candidate-card__name";
        nameEl.textContent = c.name;
        card.appendChild(nameEl);

        // Latin name
        if (c.latin) {
          const latinEl = document.createElement("div");
          latinEl.className = "candidate-card__latin";
          latinEl.textContent = c.latin;
          card.appendChild(latinEl);
        }

        // Family
        if (c.family) {
          const familyEl = document.createElement("div");
          familyEl.className = "candidate-card__family";
          familyEl.textContent = c.family;
          card.appendChild(familyEl);
        }

        // Confidence bar + source badge
        const pct = Math.round(c.confidence * 100);
        const level = pct >= 70 ? "high" : pct >= 40 ? "medium" : "low";

        const confLabel = document.createElement("div");
        confLabel.className = "confidence-label";
        const pctSpan = document.createElement("span");
        pctSpan.textContent = `${pct}%`;
        const sourceBadge = document.createElement("span");
        sourceBadge.className = `source-badge source-badge--${c.source}`;
        sourceBadge.textContent =
          c.source === "plantnet"
            ? t("identify.source_plantnet")
            : t("identify.source_claude");
        confLabel.append(pctSpan, sourceBadge);
        card.appendChild(confLabel);

        const bar = document.createElement("div");
        bar.className = "confidence-bar";
        const fill = document.createElement("div");
        fill.className = `confidence-bar__fill confidence-bar__fill--${level}`;
        fill.style.width = `${pct}%`;
        bar.appendChild(fill);
        card.appendChild(bar);

        // Add to garden button
        const addBtn = document.createElement("button");
        addBtn.type = "button";
        addBtn.className = "btn-secondary";
        addBtn.style.marginTop = "var(--sp-1)";
        addBtn.style.width = "100%";
        addBtn.textContent = t("identify.add_to_garden");
        addBtn.addEventListener("click", () => {
          cleanup();
          cbs.onAddPlant({
            name: c.name,
            latin: c.latin || c.scientific_name || "",
            category: "",
          });
        });
        card.appendChild(addBtn);

        list.appendChild(card);
      }

      body.appendChild(list);
    }

    // Attribution
    const attrLine = document.createElement("p");
    attrLine.className = "attribution-line";
    attrLine.textContent = attribution || t("identify.attribution");
    body.appendChild(attrLine);

    // Action buttons
    const actions = document.createElement("div");
    actions.className = "modal-form-actions";

    const retryBtn = document.createElement("button");
    retryBtn.type = "button";
    retryBtn.className = "btn-secondary";
    retryBtn.textContent = t("identify.try_again");
    retryBtn.addEventListener("click", () => {
      selectedFile = null;
      renderForm();
    });

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "btn-secondary";
    closeBtn.textContent = t("common.close");
    closeBtn.addEventListener("click", cleanup);

    actions.append(retryBtn, closeBtn);
    body.appendChild(actions);
  }

  function renderError(message: string): void {
    clearChildren(body);

    const errEl = document.createElement("p");
    errEl.className = "identify-error";
    errEl.textContent = message || t("identify.error");
    body.appendChild(errEl);

    const actions = document.createElement("div");
    actions.className = "modal-form-actions";

    const retryBtn = document.createElement("button");
    retryBtn.type = "button";
    retryBtn.className = "btn-secondary";
    retryBtn.textContent = t("identify.try_again");
    retryBtn.addEventListener("click", () => {
      selectedFile = null;
      renderForm();
    });

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "btn-secondary";
    closeBtn.textContent = t("common.close");
    closeBtn.addEventListener("click", cleanup);

    actions.append(retryBtn, closeBtn);
    body.appendChild(actions);
  }

  function validationMessageForFile(file: File): string | null {
    const error = validateAiPhotoUpload(file);
    if (error === "unsupported_type") {
      return t("photo_upload.error_unsupported_type");
    }
    if (error === "too_large") {
      return t("photo_upload.error_too_large");
    }
    return null;
  }

  renderForm();
}
