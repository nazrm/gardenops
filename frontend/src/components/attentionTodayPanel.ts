import { t } from "../core/i18n";
import type {
  AttentionAction,
  AttentionItem,
  AttentionPreferenceRule,
  AttentionPreferencePreset,
  AttentionPreferences,
  AttentionPreferencesUpdate,
  AttentionSection,
  AttentionSectionKey,
  AttentionTodayResponse,
} from "../core/models";

export interface AttentionTodayPanelOptions {
  fetchToday: () => Promise<AttentionTodayResponse>;
  fetchPreferences: () => Promise<AttentionPreferences>;
  updatePreferences: (preferences: AttentionPreferencesUpdate) => Promise<AttentionPreferences>;
  getRequestScope?: () => AttentionTodayRequestScope;
  onPrimaryAction?: (
    item: AttentionItem,
    action: AttentionAction,
  ) => Promise<void> | void;
  onSecondaryAction?: (
    item: AttentionItem,
    action: AttentionAction,
  ) => Promise<void> | void;
  onViewSection?: (sectionKey: AttentionSectionKey) => Promise<void> | void;
  onError?: (message: string) => void;
}

export interface AttentionTodayRequestScope {
  gardenId: number | null;
  version: number;
}

export interface AttentionTodayPanelController {
  render(feed: AttentionTodayResponse | null): void;
  setLoading(): void;
  setError(message: string): void;
  refresh(): void;
  invalidate(): void;
  closeMobileSheet(): void;
  destroy(): void;
}

const SECTION_ORDER: AttentionSectionKey[] = [
  "needs_attention",
  "warnings",
  "coming_up",
  "no_action_needed",
];
const MOBILE_COUNT_KEYS: AttentionSectionKey[] = [
  "needs_attention",
  "warnings",
  "coming_up",
];
const PRESET_OPTIONS: AttentionPreferencePreset[] = ["calm", "balanced", "detailed", "custom"];
const SEVERITY_OPTIONS: AttentionItem["severity"][] = ["low", "normal", "high", "critical"];
const MATRIX_SURFACES: Array<"panel" | "inbox" | "digest"> = ["panel", "inbox", "digest"];
const WEATHER_WATERING_METADATA_KEY = "weather_aware_watering_suppression";

interface PreferenceRuleRow {
  id: string;
  ruleKeys: string[];
  labelKey: string;
  helpKey: string;
  guardrail?: boolean;
}

const PREFERENCE_RULE_ROWS: PreferenceRuleRow[] = [
  {
    id: "routine-tasks",
    ruleKeys: ["task_due", "task_generated", "needs_action"],
    labelKey: "attention.preferences.category.routine_tasks",
    helpKey: "attention.preferences.category.routine_tasks_help",
  },
  {
    id: "overdue-tasks",
    ruleKeys: ["task_overdue"],
    labelKey: "attention.preferences.category.overdue_tasks",
    helpKey: "attention.preferences.category.overdue_tasks_help",
  },
  {
    id: "issue-follow-ups",
    ruleKeys: ["issue_follow_up_due", "issue_follow_up_overdue"],
    labelKey: "attention.preferences.category.issue_followups",
    helpKey: "attention.preferences.category.issue_followups_help",
  },
  {
    id: "weather-warnings",
    ruleKeys: [
      "warning",
      "weather_alert",
      "frost_warning",
      "rain_alert",
      "heat_wave",
      "dry_spell",
    ],
    labelKey: "attention.preferences.category.weather_warnings",
    helpKey: "attention.preferences.category.weather_warnings_help",
    guardrail: true,
  },
  {
    id: "upcoming-work",
    ruleKeys: ["upcoming", "task_upcoming", "calendar_event_due", "calendar_event_upcoming"],
    labelKey: "attention.preferences.category.upcoming_work",
    helpKey: "attention.preferences.category.upcoming_work_help",
  },
  {
    id: "no-action-history",
    ruleKeys: ["no_action_needed", "watering_covered_by_rain", "watering_rescheduled_by_rain"],
    labelKey: "attention.preferences.category.no_action_history",
    helpKey: "attention.preferences.category.no_action_history_help",
  },
  {
    id: "system-security",
    ruleKeys: ["system", "security_alert", "safety_alert"],
    labelKey: "attention.preferences.category.system_security",
    helpKey: "attention.preferences.category.system_security_help",
    guardrail: true,
  },
];

interface AttentionChildSummary {
  id: string;
  title: string;
  reason: string;
  severity: AttentionItem["severity"];
  due_on: string | null;
}

function sectionTitle(key: AttentionSectionKey): string {
  switch (key) {
    case "needs_attention":
      return t("attention.needs_attention");
    case "warnings":
      return t("attention.warnings");
    case "coming_up":
      return t("attention.coming_up");
    case "no_action_needed":
      return t("attention.no_action_needed");
  }
}

function safeTestId(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]+/g, "-");
}

function appendTextElement(
  parent: HTMLElement,
  tagName: keyof HTMLElementTagNameMap,
  className: string,
  text: string,
): HTMLElement {
  const element = document.createElement(tagName);
  element.className = className;
  element.textContent = text;
  parent.appendChild(element);
  return element;
}

function actionLabel(action: AttentionAction): string {
  if (action.label.trim()) return action.label;
  if (action.kind === "open_task") return t("attention.open_task");
  if (action.kind === "open_attention_detail") return t("attention.group_items");
  if (action.kind === "restore_attention_outcome") return t("attention.restore");
  return t("attention.open");
}

function itemCount(feed: AttentionTodayResponse | null): number {
  if (!feed) return 0;
  return MOBILE_COUNT_KEYS.reduce((total, key) => total + (feed.counts[key] ?? 0), 0);
}

function preferencePresetLabel(preset: AttentionPreferencePreset): string {
  return t(`attention.preferences.${preset}`);
}

function isPreferenceGuardrail(item: AttentionItem): boolean {
  return item.metadata["preference_guardrail"] === true;
}

function severityLabel(severity: AttentionItem["severity"]): string {
  return t(`attention.severity.${severity}`);
}

function surfaceLabel(surface: "panel" | "inbox" | "digest"): string {
  return t(`attention.preferences.surface.${surface}`);
}

function clonePreferenceRules(
  rules: AttentionPreferences["rules"],
): Record<string, AttentionPreferenceRule> {
  return Object.fromEntries(
    Object.entries(rules).map(([key, rule]) => [key, { ...rule }]),
  );
}

function cloneRecord(value: Record<string, unknown>): Record<string, unknown> {
  return { ...value };
}

function ruleForRow(
  row: PreferenceRuleRow,
  rules: Record<string, AttentionPreferenceRule>,
): AttentionPreferenceRule {
  for (const key of row.ruleKeys) {
    const rule = rules[key];
    if (rule) return { ...rule };
  }
  return { panel: true, inbox: false, digest: false, min_severity: "low" };
}

function assignRuleToRow(
  row: PreferenceRuleRow,
  rules: Record<string, AttentionPreferenceRule>,
  rule: AttentionPreferenceRule,
): void {
  row.ruleKeys.forEach((key) => {
    rules[key] = { ...rule };
  });
}

function preferenceMetadataBool(
  preferences: AttentionPreferences,
  key: string,
  fallback: boolean,
): boolean {
  const value = preferences.metadata[key];
  return typeof value === "boolean" ? value : fallback;
}

function quietHourField(
  quietHours: Record<string, unknown>,
  channel: "digest",
  field: "enabled" | "start" | "end",
  fallback: string | boolean,
): string | boolean {
  const raw = quietHours[channel];
  if (!isRecord(raw)) return fallback;
  const value = raw[field];
  if (typeof fallback === "boolean") return typeof value === "boolean" ? value : fallback;
  return typeof value === "string" ? value : fallback;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function childSummaries(item: AttentionItem): AttentionChildSummary[] {
  const children = item.metadata["children"];
  if (!Array.isArray(children)) return [];
  return children.filter(isRecord).map((child, index) => {
    const severity = String(child["severity"] || "normal");
    return {
      id: String(child["id"] || `${item.id}-child-${index}`),
      title: String(child["title"] || t("attention.open")),
      reason: String(child["reason"] || ""),
      severity: (
        ["low", "normal", "high", "critical"].includes(severity) ? severity : "normal"
      ) as AttentionItem["severity"],
      due_on: child["due_on"] ? String(child["due_on"]) : null,
    };
  });
}

export function initAttentionTodayPanel(
  options: AttentionTodayPanelOptions,
): AttentionTodayPanelController {
  const desktop = document.getElementById("attention-today-panel");
  const mobileHandle = document.getElementById("attention-today-mobile-handle");
  const mobileSheet = document.getElementById("attention-today-mobile-sheet");
  let currentFeed: AttentionTodayResponse | null = null;
  let loading = false;
  let destroyed = false;
  let refreshSequence = 0;
  let mobileKeydownBound = false;
  let preferencesDialog: HTMLElement | null = null;
  let preferencesDialogKeydownBound = false;
  let preferencesReturnFocus: HTMLButtonElement | null = null;

  function isCurrentRequestScope(
    requestScope: AttentionTodayRequestScope | undefined,
  ): boolean {
    if (!requestScope || !options.getRequestScope) return true;
    const current = options.getRequestScope();
    return current.gardenId === requestScope.gardenId
      && current.version === requestScope.version;
  }

  function mobileSheetOpen(): boolean {
    return mobileHandle instanceof HTMLButtonElement
      && mobileHandle.getAttribute("aria-expanded") === "true";
  }

  function focusableMobileSheetElements(): HTMLElement[] {
    if (!(mobileSheet instanceof HTMLElement)) return [];
    const candidates = mobileSheet.querySelectorAll<HTMLElement>(
      [
        "summary",
        "button:not([disabled])",
        "a[href]",
        "input:not([disabled])",
        "select:not([disabled])",
        "textarea:not([disabled])",
        "[tabindex]:not([tabindex='-1'])",
      ].join(","),
    );
    return Array.from(candidates).filter((element) => {
      const style = window.getComputedStyle(element);
      return style.display !== "none" && style.visibility !== "hidden";
    });
  }

  function focusFirstMobileSheetControl(): void {
    const first = focusableMobileSheetElements()[0];
    first?.focus();
  }

  function trapMobileSheetFocus(event: KeyboardEvent): void {
    if (preferencesDialog instanceof HTMLElement) return;
    if (!mobileSheetOpen() || !(mobileSheet instanceof HTMLElement)) return;
    if (event.key === "Escape") {
      event.preventDefault();
      setMobileOpen(false, true);
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = focusableMobileSheetElements();
    if (focusable.length === 0) {
      event.preventDefault();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!first || !last) {
      event.preventDefault();
      return;
    }
    const active = document.activeElement;
    if (!(active instanceof HTMLElement) || !mobileSheet.contains(active)) {
      event.preventDefault();
      first.focus();
      return;
    }
    if (event.shiftKey && active === first) {
      event.preventDefault();
      last.focus();
      return;
    }
    if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function focusablePreferencesDialogElements(): HTMLElement[] {
    if (!(preferencesDialog instanceof HTMLElement)) return [];
    const candidates = preferencesDialog.querySelectorAll<HTMLElement>(
      [
        "button:not([disabled])",
        "input:not([disabled])",
        "select:not([disabled])",
        "textarea:not([disabled])",
        "[tabindex]:not([tabindex='-1'])",
      ].join(","),
    );
    return Array.from(candidates).filter((element) => {
      const style = window.getComputedStyle(element);
      return style.display !== "none" && style.visibility !== "hidden";
    });
  }

  function closePreferencesDialog(focusReturn = true): void {
    const dialog = preferencesDialog;
    if (!(dialog instanceof HTMLElement)) return;
    preferencesDialog = null;
    if (preferencesDialogKeydownBound) {
      document.removeEventListener("keydown", trapPreferencesDialogFocus, true);
      preferencesDialogKeydownBound = false;
    }
    dialog.remove();
    if (focusReturn) {
      preferencesReturnFocus?.focus();
    }
    preferencesReturnFocus = null;
  }

  function trapPreferencesDialogFocus(event: KeyboardEvent): void {
    if (!(preferencesDialog instanceof HTMLElement)) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closePreferencesDialog(true);
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = focusablePreferencesDialogElements();
    if (focusable.length === 0) {
      event.preventDefault();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!first || !last) {
      event.preventDefault();
      return;
    }
    const active = document.activeElement;
    if (!(active instanceof HTMLElement) || !preferencesDialog.contains(active)) {
      event.preventDefault();
      first.focus();
      return;
    }
    if (event.shiftKey && active === first) {
      event.preventDefault();
      last.focus();
      return;
    }
    if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function createPresetChoice(
    preset: AttentionPreferencePreset,
    currentPreset: AttentionPreferencePreset,
  ): HTMLLabelElement {
    const label = document.createElement("label");
    label.className = "attention-preferences-choice";
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "attention-preference-preset";
    input.value = preset;
    input.checked = preset === currentPreset;
    input.setAttribute("data-testid", `attention-preferences-preset-${preset}`);
    label.appendChild(input);
    appendTextElement(
      label,
      "span",
      "attention-preferences-choice-label",
      preferencePresetLabel(preset),
    );
    return label;
  }

  function showPreferencesDialog(
    preferences: AttentionPreferences,
    returnFocus: HTMLButtonElement,
    requestScope: AttentionTodayRequestScope | undefined,
  ): void {
    closePreferencesDialog(false);
    preferencesReturnFocus = returnFocus;
    const overlay = document.createElement("div");
    overlay.className = "attention-preferences-backdrop";

    const dialog = document.createElement("section");
    dialog.className = "attention-preferences-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "attention-preferences-title");
    dialog.setAttribute("data-testid", "attention-preferences-dialog");

    const title = appendTextElement(
      dialog,
      "h3",
      "attention-preferences-title",
      t("attention.preferences.title"),
    );
    title.id = "attention-preferences-title";

    const form = document.createElement("form");
    form.className = "attention-preferences-form";
    const fieldset = document.createElement("fieldset");
    fieldset.className = "attention-preferences-fieldset";
    const legend = appendTextElement(
      fieldset,
      "legend",
      "attention-preferences-legend",
      t("attention.preferences.preset"),
    );
    legend.id = "attention-preferences-preset";
    PRESET_OPTIONS.forEach((preset) => {
      fieldset.appendChild(createPresetChoice(preset, preferences.preset));
    });
    form.appendChild(fieldset);

    const historyChoice = document.createElement("label");
    historyChoice.className = "attention-preferences-choice";
    const historyInput = document.createElement("input");
    historyInput.type = "checkbox";
    historyInput.name = "attention-show-no-action-history";
    historyInput.checked = preferences.show_no_action_history;
    historyInput.setAttribute("data-testid", "attention-preferences-show-history");
    historyChoice.appendChild(historyInput);
    appendTextElement(
      historyChoice,
      "span",
      "attention-preferences-choice-label",
      t("attention.preferences.show_no_action_history"),
    );
    form.appendChild(historyChoice);

    const customRadio = fieldset.querySelector<HTMLInputElement>(
      "input[value='custom']",
    );
    const selectCustomPreset = (): void => {
      if (customRadio) customRadio.checked = true;
    };

    const matrix = document.createElement("fieldset");
    matrix.className = "attention-preferences-fieldset attention-preferences-matrix";
    const matrixLegend = appendTextElement(
      matrix,
      "legend",
      "attention-preferences-legend",
      t("attention.preferences.delivery_matrix"),
    );
    matrixLegend.id = "attention-preferences-matrix";

    const matrixHeader = document.createElement("div");
    matrixHeader.className = "attention-preferences-matrix-header";
    appendTextElement(
      matrixHeader,
      "span",
      "attention-preferences-matrix-heading",
      t("attention.preferences.category"),
    );
    MATRIX_SURFACES.forEach((surface) => {
      appendTextElement(
        matrixHeader,
        "span",
        "attention-preferences-matrix-heading",
        surfaceLabel(surface),
      );
    });
    appendTextElement(
      matrixHeader,
      "span",
      "attention-preferences-matrix-heading",
      t("attention.preferences.minimum_severity"),
    );
    matrix.appendChild(matrixHeader);

    PREFERENCE_RULE_ROWS.forEach((rowConfig) => {
      const rule = ruleForRow(rowConfig, preferences.rules);
      const row = document.createElement("div");
      row.className = "attention-preferences-rule-row";
      row.setAttribute("data-testid", `attention-preferences-rule-${rowConfig.id}`);

      const labelWrap = document.createElement("div");
      labelWrap.className = "attention-preferences-rule-label";
      appendTextElement(labelWrap, "span", "attention-preferences-rule-title", t(rowConfig.labelKey));
      appendTextElement(labelWrap, "span", "attention-preferences-rule-help", t(rowConfig.helpKey));
      if (rowConfig.guardrail) {
        appendTextElement(
          labelWrap,
          "span",
          "attention-preferences-rule-guardrail",
          t("attention.preferences.guardrail_note"),
        );
      }
      row.appendChild(labelWrap);

      MATRIX_SURFACES.forEach((surface) => {
        const surfaceToggle = document.createElement("label");
        surfaceToggle.className = "attention-preferences-surface-toggle";
        const toggle = document.createElement("input");
        toggle.type = "checkbox";
        toggle.name = `attention-rule-${rowConfig.id}-${surface}`;
        toggle.checked = Boolean(rule[surface]);
        toggle.setAttribute(
          "aria-label",
          `${t(rowConfig.labelKey)} ${surfaceLabel(surface)}`,
        );
        toggle.setAttribute(
          "data-testid",
          `attention-preferences-rule-${rowConfig.id}-${surface}`,
        );
        toggle.addEventListener("change", selectCustomPreset);
        surfaceToggle.appendChild(toggle);
        appendTextElement(
          surfaceToggle,
          "span",
          "attention-preferences-surface-label",
          surfaceLabel(surface),
        );
        row.appendChild(surfaceToggle);
      });

      const severity = document.createElement("select");
      severity.name = `attention-rule-${rowConfig.id}-severity`;
      severity.setAttribute(
        "aria-label",
        `${t(rowConfig.labelKey)} ${t("attention.preferences.minimum_severity")}`,
      );
      severity.setAttribute(
        "data-testid",
        `attention-preferences-rule-${rowConfig.id}-severity`,
      );
      SEVERITY_OPTIONS.forEach((optionValue) => {
        const option = document.createElement("option");
        option.value = optionValue;
        option.textContent = severityLabel(optionValue);
        if ((rule.min_severity ?? "low") === optionValue) option.selected = true;
        severity.appendChild(option);
      });
      severity.addEventListener("change", selectCustomPreset);
      row.appendChild(severity);
      matrix.appendChild(row);
    });
    form.appendChild(matrix);

    const delivery = document.createElement("fieldset");
    delivery.className = "attention-preferences-fieldset attention-preferences-delivery";
    appendTextElement(
      delivery,
      "legend",
      "attention-preferences-legend",
      t("attention.preferences.delivery_controls"),
    );

    const weatherChoice = document.createElement("label");
    weatherChoice.className = "attention-preferences-choice";
    const weatherInput = document.createElement("input");
    weatherInput.type = "checkbox";
    weatherInput.name = "attention-weather-aware-watering";
    weatherInput.checked = preferenceMetadataBool(
      preferences,
      WEATHER_WATERING_METADATA_KEY,
      true,
    );
    weatherInput.setAttribute("data-testid", "attention-preferences-weather-watering");
    weatherInput.addEventListener("change", selectCustomPreset);
    weatherChoice.appendChild(weatherInput);
    appendTextElement(
      weatherChoice,
      "span",
      "attention-preferences-choice-label",
      t("attention.preferences.weather_watering"),
    );
    delivery.appendChild(weatherChoice);

    const quietGrid = document.createElement("div");
    quietGrid.className = "attention-preferences-quiet-grid";
    (["digest"] as const).forEach((channel) => {
      const quietRow = document.createElement("div");
      quietRow.className = "attention-preferences-quiet-row";
      const enabled = document.createElement("input");
      enabled.type = "checkbox";
      enabled.name = `attention-quiet-${channel}-enabled`;
      enabled.checked = Boolean(
        quietHourField(preferences.quiet_hours, channel, "enabled", false),
      );
      enabled.setAttribute(
        "data-testid",
        `attention-preferences-quiet-${channel}-enabled`,
      );
      enabled.setAttribute("aria-label", t(`attention.preferences.quiet_${channel}`));
      enabled.addEventListener("change", selectCustomPreset);
      const text = appendTextElement(
        quietRow,
        "span",
        "attention-preferences-quiet-label",
        t(`attention.preferences.quiet_${channel}`),
      );
      quietRow.prepend(enabled);
      appendTextElement(
        quietRow,
        "span",
        "attention-preferences-quiet-time-label",
        t("attention.preferences.start"),
      );
      const start = document.createElement("input");
      start.type = "time";
      start.name = `attention-quiet-${channel}-start`;
      start.value = String(quietHourField(preferences.quiet_hours, channel, "start", "22:00"));
      start.setAttribute("aria-label", `${text.textContent} ${t("attention.preferences.start")}`);
      start.setAttribute("data-testid", `attention-preferences-quiet-${channel}-start`);
      start.addEventListener("change", selectCustomPreset);
      quietRow.appendChild(start);
      appendTextElement(
        quietRow,
        "span",
        "attention-preferences-quiet-time-label",
        t("attention.preferences.end"),
      );
      const end = document.createElement("input");
      end.type = "time";
      end.name = `attention-quiet-${channel}-end`;
      end.value = String(quietHourField(preferences.quiet_hours, channel, "end", "07:00"));
      end.setAttribute("aria-label", `${text.textContent} ${t("attention.preferences.end")}`);
      end.setAttribute("data-testid", `attention-preferences-quiet-${channel}-end`);
      end.addEventListener("change", selectCustomPreset);
      quietRow.appendChild(end);
      quietGrid.appendChild(quietRow);
    });
    delivery.appendChild(quietGrid);
    form.appendChild(delivery);

    const error = appendTextElement(form, "p", "attention-preferences-error", "");
    error.hidden = true;
    error.setAttribute("role", "alert");

    const actions = document.createElement("div");
    actions.className = "attention-preferences-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "attention-today-action";
    cancel.textContent = t("common.cancel");
    cancel.setAttribute("data-testid", "attention-preferences-cancel");
    cancel.addEventListener("click", () => closePreferencesDialog(true));
    actions.appendChild(cancel);

    const save = document.createElement("button");
    save.type = "submit";
    save.className = "attention-today-action attention-today-action--primary";
    save.textContent = t("common.save");
    save.setAttribute("data-testid", "attention-preferences-save");
    actions.appendChild(save);
    form.appendChild(actions);

    function collectCustomRules(): Record<string, AttentionPreferenceRule> {
      const rules = clonePreferenceRules(preferences.rules);
      PREFERENCE_RULE_ROWS.forEach((rowConfig) => {
        const rule: AttentionPreferenceRule = {};
        MATRIX_SURFACES.forEach((surface) => {
          rule[surface] = form.querySelector<HTMLInputElement>(
            `input[name='attention-rule-${rowConfig.id}-${surface}']`,
          )?.checked ?? false;
        });
        rule.min_severity = (
          form.querySelector<HTMLSelectElement>(
            `select[name='attention-rule-${rowConfig.id}-severity']`,
          )?.value ?? "low"
        ) as AttentionItem["severity"];
        assignRuleToRow(rowConfig, rules, rule);
      });
      return rules;
    }

    function collectQuietHours(): Record<string, unknown> {
      const quietHours = cloneRecord(preferences.quiet_hours);
      (["digest"] as const).forEach((channel) => {
        quietHours[channel] = {
          enabled: form.querySelector<HTMLInputElement>(
            `input[name='attention-quiet-${channel}-enabled']`,
          )?.checked ?? false,
          start: form.querySelector<HTMLInputElement>(
            `input[name='attention-quiet-${channel}-start']`,
          )?.value ?? "22:00",
          end: form.querySelector<HTMLInputElement>(
            `input[name='attention-quiet-${channel}-end']`,
          )?.value ?? "07:00",
        };
      });
      return quietHours;
    }

    function collectMetadata(): Record<string, unknown> {
      return {
        ...preferences.metadata,
        [WEATHER_WATERING_METADATA_KEY]: form.querySelector<HTMLInputElement>(
          "input[name='attention-weather-aware-watering']",
        )?.checked ?? true,
      };
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const selected = form.querySelector<HTMLInputElement>(
        "input[name='attention-preference-preset']:checked",
      );
      const preset = (selected?.value ?? preferences.preset) as AttentionPreferencePreset;
      const showNoActionHistory = form.querySelector<HTMLInputElement>(
        "input[name='attention-show-no-action-history']",
      )?.checked ?? preferences.show_no_action_history;
      error.hidden = true;
      save.disabled = true;
      cancel.disabled = true;
      void options.updatePreferences({
        preset,
        rules: preset === "custom" ? collectCustomRules() : {},
        quiet_hours: collectQuietHours(),
        show_no_action_history: showNoActionHistory,
        metadata: collectMetadata(),
      })
        .then(() => {
          if (!isCurrentRequestScope(requestScope)) return;
          closePreferencesDialog(true);
          refresh();
        })
        .catch((err: unknown) => {
          if (!isCurrentRequestScope(requestScope)) return;
          const message = err instanceof Error ? err.message : t("attention.preferences.save_failed");
          error.textContent = message;
          error.hidden = false;
          options.onError?.(message);
        })
        .finally(() => {
          save.disabled = false;
          cancel.disabled = false;
        });
    });

    dialog.appendChild(form);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);
    preferencesDialog = overlay;
    if (!preferencesDialogKeydownBound) {
      document.addEventListener("keydown", trapPreferencesDialogFocus, true);
      preferencesDialogKeydownBound = true;
    }
    window.requestAnimationFrame(() => {
      focusablePreferencesDialogElements()[0]?.focus();
    });
  }

  async function openPreferencesDialog(button: HTMLButtonElement): Promise<void> {
    const requestScope = options.getRequestScope?.();
    if (requestScope?.gardenId === null) return;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    try {
      const preferences = await options.fetchPreferences();
      if (!isCurrentRequestScope(requestScope)) return;
      showPreferencesDialog(preferences, button, requestScope);
    } catch (err) {
      if (!isCurrentRequestScope(requestScope)) return;
      const message = err instanceof Error ? err.message : t("attention.preferences.load_failed");
      options.onError?.(message);
    } finally {
      button.disabled = false;
      button.setAttribute("aria-busy", "false");
    }
  }

  function bindMobileKeydown(): void {
    if (mobileKeydownBound) return;
    document.addEventListener("keydown", trapMobileSheetFocus, true);
    mobileKeydownBound = true;
  }

  function unbindMobileKeydown(): void {
    if (!mobileKeydownBound) return;
    document.removeEventListener("keydown", trapMobileSheetFocus, true);
    mobileKeydownBound = false;
  }

  function setMobileSheetHidden(hidden: boolean): void {
    if (!(mobileSheet instanceof HTMLElement)) return;
    mobileSheet.hidden = hidden;
    mobileSheet.setAttribute("aria-hidden", hidden ? "true" : "false");
    if (hidden) {
      mobileSheet.setAttribute("inert", "");
    } else {
      mobileSheet.removeAttribute("inert");
    }
  }

  function setMobileOpen(open: boolean, focusHandle = false): void {
    if (!(mobileHandle instanceof HTMLButtonElement) || !(mobileSheet instanceof HTMLElement)) return;
    mobileHandle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) setMobileSheetHidden(false);
    mobileSheet.classList.toggle("attention-today-mobile-sheet--open", open);
    if (open) {
      bindMobileKeydown();
      window.requestAnimationFrame(focusFirstMobileSheetControl);
      return;
    }
    unbindMobileKeydown();
    setMobileSheetHidden(true);
    if (focusHandle) mobileHandle.focus();
  }

  function syncMobileHandle(feed: AttentionTodayResponse | null): void {
    if (!(mobileHandle instanceof HTMLButtonElement)) return;
    const count = itemCount(feed);
    mobileHandle.textContent = t("attention.mobile_handle_short", { count });
    mobileHandle.setAttribute("aria-label", t("attention.mobile_handle", { count }));
  }

  async function runAction(
    item: AttentionItem,
    action: AttentionAction,
    handler: AttentionTodayPanelOptions["onPrimaryAction"],
    button: HTMLButtonElement,
  ): Promise<void> {
    if (!handler) return;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    try {
      await handler(item, action);
    } finally {
      button.disabled = false;
      button.setAttribute("aria-busy", "false");
    }
  }

  function createActionButton(
    item: AttentionItem,
    action: AttentionAction,
    kind: "primary" | "secondary",
  ): HTMLButtonElement {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `attention-today-action attention-today-action--${kind}`;
    button.textContent = actionLabel(action);
    button.setAttribute("data-testid", `attention-today-${kind}-action-${safeTestId(item.id)}`);
    button.addEventListener("click", () => {
      const handler = kind === "primary" ? options.onPrimaryAction : options.onSecondaryAction;
      void runAction(item, action, handler, button);
    });
    return button;
  }

  function createChildDetails(children: AttentionChildSummary[]): HTMLElement {
    const details = document.createElement("details");
    details.className = "attention-today-child-details";
    const summary = document.createElement("summary");
    summary.className = "attention-today-child-summary";
    summary.textContent = t("attention.group_items_count", { count: children.length });
    details.appendChild(summary);

    const list = document.createElement("ul");
    list.className = "attention-today-child-list";
    children.forEach((child) => {
      const row = document.createElement("li");
      row.className = "attention-today-child-item";
      appendTextElement(row, "span", "attention-today-child-title", child.title);
      const metaParts = [
        severityLabel(child.severity),
        child.reason,
        child.due_on,
      ].filter(Boolean);
      appendTextElement(row, "span", "attention-today-child-meta", metaParts.join(" - "));
      list.appendChild(row);
    });
    details.appendChild(list);
    return details;
  }

  function createItem(item: AttentionItem): HTMLElement {
    const article = document.createElement("article");
    article.className = `attention-today-item attention-today-item--${item.severity}`;
    article.setAttribute("data-testid", `attention-today-item-${safeTestId(item.id)}`);
    article.setAttribute("aria-label", `${item.title} - ${severityLabel(item.severity)}`);

    const header = document.createElement("div");
    header.className = "attention-today-item-header";
    appendTextElement(header, "h4", "attention-today-item-title", item.title);
    if (item.source_label) {
      appendTextElement(header, "span", "attention-today-source", item.source_label);
    }
    article.appendChild(header);

    const meta = document.createElement("p");
    meta.className = "attention-today-meta";
    meta.textContent = [severityLabel(item.severity), item.reason, item.due_on]
      .filter(Boolean)
      .join(" - ");
    article.appendChild(meta);

    if (item.body) {
      appendTextElement(article, "p", "attention-today-body", item.body);
    }

    if (isPreferenceGuardrail(item)) {
      appendTextElement(
        article,
        "p",
        "attention-today-guardrail",
        t("attention.guardrail_visible"),
      );
    }

    const children = childSummaries(item);
    if (children.length > 0) {
      article.appendChild(createChildDetails(children));
    }

    const actions = document.createElement("div");
    actions.className = "attention-today-actions";
    if (item.primary_action && item.primary_action.kind !== "open_attention_detail") {
      actions.appendChild(createActionButton(item, item.primary_action, "primary"));
    }
    item.secondary_actions.forEach((action) => {
      actions.appendChild(createActionButton(item, action, "secondary"));
    });
    if (actions.childElementCount > 0) {
      article.appendChild(actions);
    }

    return article;
  }

  function createSection(section: AttentionSection, surfaceId: string): HTMLElement {
    const details = document.createElement("details");
    details.className = "attention-today-section";
    details.open = section.key !== "no_action_needed";
    details.setAttribute("data-testid", `attention-today-section-${section.key}`);
    const listId = `attention-today-list-${surfaceId}-${section.key}`;

    const summary = document.createElement("summary");
    summary.className = "attention-today-section-summary";
    summary.setAttribute("aria-expanded", details.open ? "true" : "false");
    summary.setAttribute("aria-controls", listId);
    appendTextElement(summary, "span", "attention-today-section-title", sectionTitle(section.key));
    appendTextElement(summary, "span", "attention-today-section-count", String(section.count));
    details.appendChild(summary);

    const list = document.createElement("div");
    list.id = listId;
    list.className = "attention-today-list";
    if (section.items.length === 0) {
      appendTextElement(list, "p", "attention-today-empty", t("attention.empty_section"));
    } else {
      section.items.forEach((item) => list.appendChild(createItem(item)));
    }
    if (section.count > section.items.length) {
      const overflow = document.createElement("div");
      overflow.className = "attention-today-overflow";
      appendTextElement(
        overflow,
        "span",
        "attention-today-overflow-count",
        t("attention.more_items", { count: section.count - section.items.length }),
      );
      if (options.onViewSection) {
        const viewAll = document.createElement("button");
        viewAll.type = "button";
        viewAll.className = "attention-today-action";
        viewAll.textContent = t("attention.view_all");
        viewAll.setAttribute(
          "data-testid",
          `attention-today-view-all-${safeTestId(section.key)}`,
        );
        viewAll.addEventListener("click", () => {
          void options.onViewSection?.(section.key);
        });
        overflow.appendChild(viewAll);
      }
      list.appendChild(overflow);
    }
    details.appendChild(list);
    details.addEventListener("toggle", () => {
      summary.setAttribute("aria-expanded", details.open ? "true" : "false");
    });
    return details;
  }

  function createHeader(titleId: string, includeClose: boolean): HTMLElement {
    const header = document.createElement("div");
    header.className = "attention-today-header";
    const titleWrap = document.createElement("div");
    appendTextElement(titleWrap, "p", "attention-today-kicker", t("attention.today"));
    const title = appendTextElement(titleWrap, "h3", "attention-today-title", t("attention.today"));
    title.id = titleId;
    header.appendChild(titleWrap);

    const actions = document.createElement("div");
    actions.className = "attention-today-header-actions";
    const settings = document.createElement("button");
    settings.type = "button";
    settings.className = "attention-today-icon-btn";
    settings.textContent = t("attention.settings_short");
    settings.setAttribute("aria-label", t("attention.settings"));
    settings.title = t("attention.settings");
    settings.setAttribute("data-testid", "attention-today-settings");
    settings.addEventListener("click", () => {
      void openPreferencesDialog(settings);
    });
    actions.appendChild(settings);

    if (includeClose) {
      const close = document.createElement("button");
      close.type = "button";
      close.className = "attention-today-icon-btn attention-today-close";
      close.textContent = t("common.close");
      close.setAttribute("aria-label", t("attention.close"));
      close.setAttribute("data-testid", "attention-today-mobile-close");
      close.addEventListener("click", () => setMobileOpen(false, true));
      actions.appendChild(close);
    }

    header.appendChild(actions);
    return header;
  }

  function createContent(
    feed: AttentionTodayResponse | null,
    titleId: string,
    includeClose: boolean,
    surfaceId: string,
  ): HTMLElement {
    const root = document.createElement("div");
    root.className = "attention-today-content";
    root.appendChild(createHeader(titleId, includeClose));

    if (!feed) {
      appendTextElement(root, "p", "attention-today-empty", t("attention.empty"));
      return root;
    }

    if (feed.degraded_providers.length > 0) {
      const note = appendTextElement(root, "p", "attention-today-degraded", t("attention.degraded"));
      note.setAttribute("data-testid", "attention-today-degraded");
    }

    const sectionsByKey = new Map(feed.sections.map((section) => [section.key, section]));
    SECTION_ORDER.forEach((key) => {
      const section = sectionsByKey.get(key) ?? { key, count: 0, items: [] };
      root.appendChild(createSection(section, surfaceId));
    });
    return root;
  }

  function render(feed: AttentionTodayResponse | null): void {
    if (destroyed) return;
    currentFeed = feed;
    loading = false;
    syncMobileHandle(feed);
    if (desktop instanceof HTMLElement) {
      desktop.replaceChildren(createContent(feed, "attention-today-title", false, "desktop"));
    }
    if (mobileSheet instanceof HTMLElement) {
      mobileSheet.replaceChildren(
        createContent(feed, "attention-today-mobile-title", true, "mobile"),
      );
    }
  }

  function setLoading(): void {
    if (destroyed) return;
    loading = true;
    syncMobileHandle(currentFeed);
    const content = document.createElement("div");
    content.className = "attention-today-content";
    content.appendChild(createHeader("attention-today-title", false));
    appendTextElement(content, "p", "attention-today-empty", t("common.loading"));
    if (desktop instanceof HTMLElement) desktop.replaceChildren(content);
    if (mobileSheet instanceof HTMLElement) {
      const mobileContent = document.createElement("div");
      mobileContent.className = "attention-today-content";
      mobileContent.appendChild(createHeader("attention-today-mobile-title", true));
      appendTextElement(mobileContent, "p", "attention-today-empty", t("common.loading"));
      mobileSheet.replaceChildren(mobileContent);
    }
  }

  function setError(message: string): void {
    if (destroyed) return;
    loading = false;
    syncMobileHandle(currentFeed);
    const content = document.createElement("div");
    content.className = "attention-today-content";
    content.appendChild(createHeader("attention-today-title", false));
    appendTextElement(content, "p", "attention-today-error", message);
    if (desktop instanceof HTMLElement) desktop.replaceChildren(content);
    if (mobileSheet instanceof HTMLElement) {
      const mobileContent = document.createElement("div");
      mobileContent.className = "attention-today-content";
      mobileContent.appendChild(createHeader("attention-today-mobile-title", true));
      appendTextElement(mobileContent, "p", "attention-today-error", message);
      mobileSheet.replaceChildren(mobileContent);
    }
  }

  function refresh(): void {
    if (destroyed) return;
    const requestScope = options.getRequestScope?.();
    const requestId = ++refreshSequence;
    setLoading();
    if (requestScope?.gardenId === null) {
      render(null);
      return;
    }
    void options.fetchToday()
      .then((feed) => {
        if (
          requestId !== refreshSequence
          || destroyed
          || !isCurrentRequestScope(requestScope)
          || (requestScope && feed.garden_id !== requestScope.gardenId)
        ) {
          return;
        }
        render(feed);
      })
      .catch((err: unknown) => {
        if (
          requestId !== refreshSequence
          || destroyed
          || !isCurrentRequestScope(requestScope)
        ) {
          return;
        }
        const message = err instanceof Error ? err.message : t("attention.load_failed");
        setError(message);
        options.onError?.(message);
      });
  }

  function handleMobileHandleClick(): void {
    if (!(mobileHandle instanceof HTMLButtonElement)) return;
    const open = mobileHandle.getAttribute("aria-expanded") !== "true";
    setMobileOpen(open);
  }

  function invalidate(): void {
    if (destroyed) return;
    refreshSequence += 1;
    loading = false;
    currentFeed = null;
    closePreferencesDialog(false);
    setMobileOpen(false);
    render(null);
  }

  function destroy(): void {
    if (destroyed) return;
    invalidate();
    destroyed = true;
    if (mobileHandle instanceof HTMLButtonElement) {
      mobileHandle.removeEventListener("click", handleMobileHandleClick);
    }
    unbindMobileKeydown();
  }

  function closeMobileSheet(): void {
    setMobileOpen(false);
  }

  if (mobileHandle instanceof HTMLButtonElement) {
    mobileHandle.addEventListener("click", handleMobileHandleClick);
  }

  setMobileSheetHidden(true);
  syncMobileHandle(null);

  return {
    render,
    setLoading,
    setError,
    refresh,
    invalidate,
    closeMobileSheet,
    destroy,
  };
}
