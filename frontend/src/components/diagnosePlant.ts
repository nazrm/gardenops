import { t } from "../core/i18n";
import { createModal } from "./dialogCore";
import {
  AI_PHOTO_UPLOAD_ACCEPT,
  diagnosePlantApi,
  createIssueApi,
  uploadMediaApi,
  getApiErrorMessage,
  validateAiPhotoUpload,
} from "../services/api";
import type { DiagnosisCandidate } from "../services/api";
import { clearChildren } from "../core/sanitize";

export interface DiagnosePlantCallbacks {
  onIssueCreated: (issueId: string) => void;
  onClose: () => void;
}

export function showDiagnosePlantModal(
  pltId: string,
  plotIds: string[],
  plantName: string,
  cbs: DiagnosePlantCallbacks,
): void {
  const { dialog, close } = createModal(
    t("diagnose.title"),
    `<div class="modal-content diagnose-modal">
      <h2 class="modal-title">${t("diagnose.title")}</h2>
      <div id="diagnose-body"></div>
    </div>`,
  );

  const body = dialog.querySelector<HTMLElement>("#diagnose-body")!;
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

    const plotContext = plotIds.length === 0
      ? ""
      : plotIds.length === 1
        ? plotIds[0]!
        : `${plotIds[0]!}, +${plotIds.length - 1}`;

    // Context line
    if (plantName) {
      const ctx = document.createElement("p");
      ctx.className = "diagnose-context";
      ctx.textContent = plotContext
        ? t("diagnose.context_label", { name: plantName, plot: plotContext })
        : plantName;
      body.appendChild(ctx);
    }

    // File input
    const fileLabel = document.createElement("label");
    fileLabel.className = "modal-field-label";
    fileLabel.textContent = t("diagnose.select_photo");
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

    // Symptoms textarea
    const sympLabel = document.createElement("label");
    sympLabel.className = "modal-field-label";
    sympLabel.textContent = t("diagnose.symptoms_label");
    sympLabel.style.marginTop = "var(--sp-2)";
    body.appendChild(sympLabel);

    const sympInput = document.createElement("textarea");
    sympInput.className = "modal-textarea";
    sympInput.placeholder = t("diagnose.symptoms_placeholder");
    sympInput.maxLength = 500;
    sympInput.rows = 3;
    body.appendChild(sympInput);

    // Actions
    const actions = document.createElement("div");
    actions.className = "modal-form-actions";

    const diagnoseBtn = document.createElement("button");
    diagnoseBtn.type = "button";
    diagnoseBtn.className = "btn-primary";
    diagnoseBtn.textContent = t("diagnose.button");
    diagnoseBtn.disabled = true;

    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "btn-secondary";
    cancelBtn.textContent = t("common.close");

    actions.append(diagnoseBtn, cancelBtn);
    body.appendChild(actions);

    // Events
    fileInput.addEventListener("change", () => {
      const file = fileInput.files?.[0];
      if (!file) return;
      const validationMessage = validationMessageForFile(file);
      if (validationMessage) {
        selectedFile = null;
        diagnoseBtn.disabled = true;
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
      diagnoseBtn.disabled = false;
    });

    cancelBtn.addEventListener("click", cleanup);

    diagnoseBtn.addEventListener("click", () => {
      if (!selectedFile) return;
      void runDiagnosis(selectedFile, sympInput.value.trim());
    });
  }

  async function runDiagnosis(file: File, symptoms: string): Promise<void> {
    const validationMessage = validationMessageForFile(file);
    if (validationMessage) {
      renderError(validationMessage);
      return;
    }
    clearChildren(body);
    const loader = document.createElement("div");
    loader.className = "identify-loading";
    loader.textContent = t("diagnose.loading");
    body.appendChild(loader);

    try {
      const opts: Parameters<typeof diagnosePlantApi>[0] = { image: file };
      if (pltId) opts.pltId = pltId;
      const primaryPlotId = plotIds[0];
      if (primaryPlotId) opts.plotId = primaryPlotId;
      if (symptoms) opts.symptoms = symptoms;
      const result = await diagnosePlantApi(opts);
      renderResults(result.diagnoses, result.disclaimer, file);
    } catch (err) {
      renderError(getApiErrorMessage(err));
    }
  }

  function renderResults(
    diagnoses: DiagnosisCandidate[],
    disclaimer: string,
    photo: File,
  ): void {
    clearChildren(body);

    // Disclaimer
    const discEl = document.createElement("p");
    discEl.className = "diagnosis-disclaimer";
    discEl.textContent = disclaimer || t("diagnose.disclaimer");
    body.appendChild(discEl);

    if (diagnoses.length === 0) {
      const healthy = document.createElement("p");
      healthy.className = "diagnose-healthy";
      healthy.textContent = t("diagnose.no_issues");
      body.appendChild(healthy);
    } else {
      for (const d of diagnoses) {
        const card = document.createElement("div");
        card.className = "diagnosis-card";

        // Header: confidence badge + issue type tag
        const header = document.createElement("div");
        header.className = "diagnosis-card__header";

        const confBadge = document.createElement("span");
        confBadge.className = `confidence-badge confidence-badge--${d.confidence}`;
        confBadge.textContent = t(`diagnose.confidence_${d.confidence}`);
        header.appendChild(confBadge);

        const typeTag = document.createElement("span");
        typeTag.className = "issue-type-tag";
        typeTag.textContent = d.issue_type;
        header.appendChild(typeTag);

        card.appendChild(header);

        // Cause name
        const cause = document.createElement("div");
        cause.className = "diagnosis-card__cause";
        cause.textContent = d.likely_cause;
        card.appendChild(cause);

        // Description
        const desc = document.createElement("p");
        desc.textContent = d.description;
        card.appendChild(desc);

        // Treatment
        if (d.suggested_treatment) {
          const treatLabel = document.createElement("div");
          treatLabel.className = "diagnosis-card__treatment-label";
          treatLabel.textContent = t("diagnose.treatment");
          card.appendChild(treatLabel);

          const treat = document.createElement("div");
          treat.className = "diagnosis-card__treatment";
          treat.textContent = d.suggested_treatment;
          card.appendChild(treat);
        }

        // Related history
        if (d.related_history) {
          const hist = document.createElement("div");
          hist.className = "diagnosis-card__history";
          hist.textContent = d.related_history;
          card.appendChild(hist);
        }

        // Track this issue button
        const trackBtn = document.createElement("button");
        trackBtn.type = "button";
        trackBtn.className = "btn-secondary";
        trackBtn.style.marginTop = "var(--sp-1)";
        trackBtn.style.width = "100%";
        trackBtn.textContent = t("diagnose.track_issue");
        trackBtn.addEventListener("click", () => {
          void createIssueFromDiagnosis(d, photo, trackBtn);
        });
        card.appendChild(trackBtn);

        body.appendChild(card);
      }
    }

    // Action buttons
    const actions = document.createElement("div");
    actions.className = "modal-form-actions";

    const retryBtn = document.createElement("button");
    retryBtn.type = "button";
    retryBtn.className = "btn-secondary";
    retryBtn.textContent = t("diagnose.try_again");
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

  async function createIssueFromDiagnosis(
    d: DiagnosisCandidate,
    photo: File,
    btn: HTMLButtonElement,
  ): Promise<void> {
    btn.disabled = true;
    btn.textContent = t("diagnose.loading");
    try {
      const result = await createIssueApi({
        issue_type: d.issue_type,
        title: d.likely_cause,
        description: d.description,
        severity: d.confidence === "high" ? "high" : "normal",
        suspected_cause: d.likely_cause,
        treatment_plan: d.suggested_treatment,
        plant_ids: pltId ? [pltId] : [],
        plot_ids: plotIds,
      });

      // Attach photo to the new issue
      try {
        await uploadMediaApi({
          targetType: "issue",
          targetId: result.id,
          file: photo,
        });
      } catch {
        // Photo attachment failure is non-critical
      }

      btn.textContent = t("diagnose.issue_created");
      btn.classList.add("btn-success");
      cbs.onIssueCreated(result.id);
      cleanup();
    } catch (err) {
      btn.textContent = getApiErrorMessage(err);
      btn.disabled = false;
    }
  }

  function renderError(message: string): void {
    clearChildren(body);

    const errEl = document.createElement("p");
    errEl.className = "identify-error";
    errEl.textContent = message || t("diagnose.error");
    body.appendChild(errEl);

    const actions = document.createElement("div");
    actions.className = "modal-form-actions";

    const retryBtn = document.createElement("button");
    retryBtn.type = "button";
    retryBtn.className = "btn-secondary";
    retryBtn.textContent = t("diagnose.try_again");
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
