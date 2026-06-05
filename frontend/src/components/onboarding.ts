import { escapeHtml, setReviewedDynamicHtml } from "../core/sanitize";
import { getLocale, setLocale, subscribeLocaleChange, t } from "../core/i18n";
import {
  completeGardenOnboardingApi,
  getGardenSettingsApi,
  updateGardenSettingsApi,
  updateAuthMeSettingsApi,
} from "../services/api";

export interface OnboardingCallbacks {
  onComplete: () => void;
  onDismiss?: () => void;
  canDismiss?: boolean;
  gardenId: number;
  gardenName: string;
  username: string;
}

interface OnboardingState {
  step: number;
  gardenName: string;
  gridRows: number;
  gridCols: number;
  houseRow: number;
  houseCol: number;
  houseWidth: number;
  houseHeight: number;
  skipHouse: boolean;
  latitude: number | null;
  longitude: number | null;
  address: string;
  zones: ZoneEntry[];
}

interface ZoneEntry {
  code: string;
  name: string;
  startRow: number;
  startCol: number;
  endRow: number;
  endCol: number;
  color: string;
}

const ZONE_COLORS = [
  "#4a7c59", "#6b8e5a", "#8fbc8f", "#c4a265",
  "#7b6b8a", "#5b8a9a", "#d4956a", "#9acd32",
];

const STEP_TITLE_KEYS = [
  "onboarding.step_welcome",
  "onboarding.step_name",
  "onboarding.step_property",
  "onboarding.step_house",
  "onboarding.step_location",
  "onboarding.step_zones",
  "onboarding.step_ready",
] as const;

const TOTAL_STEPS = STEP_TITLE_KEYS.length;

function getStepTitles(): string[] {
  return STEP_TITLE_KEYS.map((key) => t(key));
}

function renderLocaleSwitch(scope: string): string {
  const locale = getLocale();
  return `
    <div class="locale-switch onboarding-locale-switch" role="group" aria-label="${escapeHtml(t("common.language"))}">
      <button
        type="button"
        class="locale-switch-btn${locale === "en" ? " active" : ""}"
        data-locale-option="en"
        data-locale-scope="${scope}"
        aria-pressed="${locale === "en" ? "true" : "false"}"
        lang="en"
      >EN</button>
      <button
        type="button"
        class="locale-switch-btn${locale === "no" ? " active" : ""}"
        data-locale-option="no"
        data-locale-scope="${scope}"
        aria-pressed="${locale === "no" ? "true" : "false"}"
        lang="no"
      >NO</button>
    </div>
  `;
}

interface HousePreviewState {
  row: number;
  col: number;
  width: number;
  height: number;
  north_degrees: number;
  grid_rows: number;
  grid_cols: number;
}

interface ManualSetupSummary {
  house: HousePreviewState;
  usesDefaultHouse: boolean;
  zoneGroupCount: number;
  requestedPlots: number;
  effectivePlots: number;
  skippedPlots: number;
  warnings: string[];
  errors: string[];
}

function getDefaultHouseState(gridRows: number, gridCols: number): HousePreviewState {
  const safeGridRows = Math.max(5, Math.min(Math.trunc(gridRows || 30), 100));
  const safeGridCols = Math.max(5, Math.min(Math.trunc(gridCols || 22), 100));
  const width = Math.max(1, Math.min(12, safeGridCols));
  const height = Math.max(1, Math.min(8, safeGridRows));
  const maxRow = Math.max(1, safeGridRows - height + 1);
  const maxCol = Math.max(1, safeGridCols - width + 1);
  return {
    row: Math.max(1, Math.min(9, maxRow)),
    col: Math.max(1, Math.min(6, maxCol)),
    width,
    height,
    north_degrees: 0,
    grid_rows: safeGridRows,
    grid_cols: safeGridCols,
  };
}

function getEffectiveHouseState(state: OnboardingState): {
  house: HousePreviewState;
  usesDefaultHouse: boolean;
} {
  if (state.skipHouse) {
    return {
      house: getDefaultHouseState(state.gridRows, state.gridCols),
      usesDefaultHouse: true,
    };
  }
  return {
    house: {
      row: state.houseRow,
      col: state.houseCol,
      width: state.houseWidth,
      height: state.houseHeight,
      north_degrees: 0,
      grid_rows: state.gridRows,
      grid_cols: state.gridCols,
    },
    usesDefaultHouse: false,
  };
}

function summarizeManualSetup(state: OnboardingState): ManualSetupSummary {
  const { house, usesDefaultHouse } = getEffectiveHouseState(state);
  const warningSet = new Set<string>();
  const errorSet = new Set<string>();
  const seenCells = new Map<string, string>();
  const zoneCodes = new Set<string>();
  let requestedPlots = 0;
  let overlappingZoneCells = 0;

  for (const zone of state.zones) {
    const code = zone.code.trim();
    const name = zone.name.trim();
    if (!code || !name) {
      errorSet.add(t("onboarding.zone_missing_fields"));
      continue;
    }
    if (zone.startRow > zone.endRow || zone.startCol > zone.endCol) {
      errorSet.add(t("onboarding.zone_invalid_bounds", { code }));
      continue;
    }
    if (
      zone.startRow < 1
      || zone.startCol < 1
      || zone.endRow > state.gridRows
      || zone.endCol > state.gridCols
    ) {
      errorSet.add(t("onboarding.zone_outside_grid", {
        code,
        cols: state.gridCols,
        rows: state.gridRows,
      }));
      continue;
    }
    if (zoneCodes.has(code)) {
      warningSet.add(t("onboarding.zone_duplicate", { code }));
    }
    zoneCodes.add(code);
    let reportedHouseOverlap = false;
    for (let row = zone.startRow; row <= zone.endRow; row += 1) {
      for (let col = zone.startCol; col <= zone.endCol; col += 1) {
        requestedPlots += 1;
        const insideHouse =
          row >= house.row
          && row <= house.row + house.height - 1
          && col >= house.col
          && col <= house.col + house.width - 1;
        if (insideHouse) {
          if (!reportedHouseOverlap) {
            errorSet.add(
              t("onboarding.zone_overlaps_house", {
                code,
                houseLabel: t(usesDefaultHouse ? "onboarding.default_house" : "onboarding.house_label"),
              }),
            );
            reportedHouseOverlap = true;
          }
          continue;
        }
        const key = `${row}:${col}`;
        if (seenCells.has(key)) {
          overlappingZoneCells += 1;
          continue;
        }
        seenCells.set(key, code);
      }
    }
  }

  if (overlappingZoneCells > 0) {
    warningSet.add(t("onboarding.zone_overlap_cells", { count: overlappingZoneCells }));
  }

  return {
    house,
    usesDefaultHouse,
    zoneGroupCount: zoneCodes.size,
    requestedPlots,
    effectivePlots: seenCells.size,
    skippedPlots: requestedPlots - seenCells.size,
    warnings: Array.from(warningSet),
    errors: Array.from(errorSet),
  };
}

function renderIssueBox(
  title: string,
  items: string[],
  variant: "warning" | "error",
): string {
  if (items.length === 0) return "";
  return `
    <div class="onb-validation onb-validation--${variant}">
      <strong>${escapeHtml(title)}</strong>
      <ul>
        ${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    </div>
  `;
}

export function showOnboarding(
  container: HTMLElement,
  cbs: OnboardingCallbacks,
): { destroy: () => void } {
  const state: OnboardingState = {
    step: 0,
    gardenName: cbs.gardenName,
    gridRows: 30,
    gridCols: 22,
    houseRow: 9,
    houseCol: 6,
    houseWidth: 12,
    houseHeight: 8,
    skipHouse: false,
    latitude: null,
    longitude: null,
    address: "",
    zones: [],
  };

  // Load existing settings.
  void getGardenSettingsApi(cbs.gardenId).then((s) => {
    state.gardenName = s.name;
    state.gridRows = s.grid_rows;
    state.gridCols = s.grid_cols;
    if (s.latitude != null) state.latitude = s.latitude;
    if (s.longitude != null) state.longitude = s.longitude;
    state.address = s.address || "";
    render();
  }).catch(() => { /* use defaults */ });

  const el = document.createElement("div");
  el.className = "onboarding-overlay";
  const stopLocaleSubscription = subscribeLocaleChange(() => {
    render();
  });
  // Block escape key
  const blockEscape = (e: KeyboardEvent) => {
    if (e.key === "Escape" && el.contains(document.activeElement ?? document.body)) {
      e.preventDefault(); e.stopPropagation();
    }
  };
  document.addEventListener("keydown", blockEscape, true);
  container.appendChild(el);

  function render(): void {
    const stepTitles = getStepTitles();
    setReviewedDynamicHtml(el, `
      <div class="onboarding-card">
        <div class="onboarding-card-header">
          <span class="onboarding-card-language-label">${escapeHtml(t("common.language"))}</span>
          ${renderLocaleSwitch("onboarding")}
        </div>
        <div class="onboarding-progress">
          ${stepTitles.map((title, i) => `
            <div class="onboarding-step-dot ${i === state.step ? "active" : ""} ${i < state.step ? "done" : ""}"
                 title="${escapeHtml(title)}">
              <span>${i < state.step ? "&#10003;" : i + 1}</span>
            </div>
            ${i < stepTitles.length - 1 ? '<div class="onboarding-step-line"></div>' : ""}
          `).join("")}
        </div>
        <div class="onboarding-progress-bar">
          <div class="onboarding-progress-fill" style="width: ${Math.round(((state.step + 1) / TOTAL_STEPS) * 100)}%"></div>
        </div>
        <div class="onboarding-progress-label">${t("onboarding.progress_label", { current: state.step + 1, total: TOTAL_STEPS })}</div>
        <div class="onboarding-body">
          ${renderStep()}
        </div>
        <div class="onboarding-nav">
          ${renderNav()}
        </div>
      </div>
    `);
    wireStep();
  }

  function renderNav(): string {
    const dismissBtn = cbs.canDismiss
      ? `<button type="button" class="onb-dismiss">${escapeHtml(t("onboarding.dismiss"))}</button>`
      : "";
    const backBtn = state.step > 0
      ? `<button type="button" class="onb-back">${escapeHtml(t("onboarding.back"))}</button>`
      : '<span></span>';
    const nextBtn = state.step < TOTAL_STEPS - 1
      ? `<button type="button" class="onb-next">${escapeHtml(t("onboarding.next"))}</button>`
      : `<button type="button" class="onb-finish">${escapeHtml(t("onboarding.create_garden"))}</button>`;
    return `
      <div class="onb-nav-left">${dismissBtn}</div>
      <div class="onb-nav-right">${backBtn}${nextBtn}</div>
    `;
  }

  function renderStep(): string {
    switch (state.step) {
      case 0: return renderWelcome();
      case 1: return renderNameIt();
      case 2: return renderPropertySize();
      case 3: return renderHousePlacement();
      case 4: return renderLocation();
      case 5: return renderZones();
      case 6: return renderComplete();
      default: return "";
    }
  }

  function gardenSettingsLabel(): string {
    return cbs.canDismiss
      ? t("onboarding.settings_label_admin")
      : t("onboarding.settings_label_user");
  }

  function nextStepsSettingsCard(): string {
    if (cbs.canDismiss) {
      return `
        <div class="onb-intro-feature">
          <strong>${escapeHtml(t("onboarding.next_settings_title_admin"))}</strong>
          <span>${escapeHtml(t("onboarding.next_settings_body_admin"))}</span>
        </div>
      `;
    }
    return `
      <div class="onb-intro-feature">
        <strong>${escapeHtml(t("onboarding.next_settings_title_user"))}</strong>
        <span>${escapeHtml(t("onboarding.next_settings_body_user"))}</span>
      </div>
    `;
  }

  // ── Step renderers ──

  function renderWelcome(): string {
    return `
      <div class="onb-intro">
        <h2>${escapeHtml(t("onboarding.welcome_title"))}</h2>
        <p>${escapeHtml(t("onboarding.welcome_body"))}</p>

        <div class="onb-intro-features">
          <div class="onb-intro-feature">
            <strong>${escapeHtml(t("onboarding.feature_map_title"))}</strong>
            <span>${escapeHtml(t("onboarding.feature_map_body"))}</span>
          </div>
          <div class="onb-intro-feature">
            <strong>${escapeHtml(t("onboarding.feature_zones_title"))}</strong>
            <span>${escapeHtml(t("onboarding.feature_zones_body"))}</span>
          </div>
          <div class="onb-intro-feature">
            <strong>${escapeHtml(t("onboarding.feature_plants_title"))}</strong>
            <span>${escapeHtml(t("onboarding.feature_plants_body"))}</span>
          </div>
          <div class="onb-intro-feature">
            <strong>${escapeHtml(t("onboarding.feature_shade_title"))}</strong>
            <span>${escapeHtml(t("onboarding.feature_shade_body"))}</span>
          </div>
          <div class="onb-intro-feature">
            <strong>${escapeHtml(t("onboarding.feature_analysis_title"))}</strong>
            <span>${escapeHtml(t("onboarding.feature_analysis_body"))}</span>
          </div>
        </div>

        <h3>${escapeHtml(t("onboarding.good_to_know"))}</h3>
        <ul class="onb-intro-nots">
          <li>${escapeHtml(t("onboarding.note_1"))}</li>
          <li>${escapeHtml(t("onboarding.note_2"))}</li>
          <li>${escapeHtml(t("onboarding.note_3"))}</li>
          <li>${escapeHtml(t("onboarding.note_4"))}</li>
        </ul>

        <div class="onb-intro-sentence">
          <p>${escapeHtml(t(cbs.canDismiss ? "onboarding.welcome_sentence_admin" : "onboarding.welcome_sentence_user"))}</p>
        </div>
      </div>
    `;
  }

  function renderNameIt(): string {
    return `
      <div class="onb-welcome">
        <h2>${escapeHtml(t("onboarding.name_title"))}</h2>
        <p>${escapeHtml(t("onboarding.name_body"))}</p>
        <label class="onb-field">
          <span>${escapeHtml(t("onboarding.garden_name"))}</span>
          <input type="text" id="onb-garden-name"
            value="${escapeHtml(state.gardenName)}"
            placeholder="${escapeHtml(t("onboarding.garden_name_placeholder"))}"
            maxlength="120" />
        </label>
        <p class="onb-hint">${escapeHtml(t("onboarding.rename_later", { settingsLabel: gardenSettingsLabel() }))}</p>
      </div>
    `;
  }

  function renderPropertySize(): string {
    return `
      <div class="onb-property">
        <h2>${escapeHtml(t("onboarding.property_title"))}</h2>
        <p>${escapeHtml(t("onboarding.property_body"))}</p>
        <div class="onb-dims">
          <label class="onb-field">
            <span>${escapeHtml(t("onboarding.width"))}</span>
            <input type="number" id="onb-cols" min="5" max="100"
              value="${state.gridCols}" />
          </label>
          <span class="onb-dims-x">&times;</span>
          <label class="onb-field">
            <span>${escapeHtml(t("onboarding.depth"))}</span>
            <input type="number" id="onb-rows" min="5" max="100"
              value="${state.gridRows}" />
          </label>
        </div>
        <div class="onb-grid-preview" id="onb-grid-preview"></div>
        <p class="onb-hint">${escapeHtml(t("onboarding.total_area", {
          cols: state.gridCols,
          rows: state.gridRows,
          area: state.gridCols * state.gridRows,
        }))}</p>
      </div>
    `;
  }

  function renderHousePlacement(): string {
    const manualSummary = summarizeManualSetup(state);
    return `
      <div class="onb-house">
        <h2>${escapeHtml(t("onboarding.house_title"))}</h2>
        <p>${escapeHtml(t("onboarding.house_body"))}</p>
        <label class="onb-check">
          <input type="checkbox" id="onb-skip-house"
            ${state.skipHouse ? "checked" : ""} />
          <span>${escapeHtml(t("onboarding.house_use_default"))}</span>
        </label>
        <p class="onb-hint">${state.skipHouse
          ? escapeHtml(t("onboarding.house_default_hint", {
            width: manualSummary.house.width,
            height: manualSummary.house.height,
            row: manualSummary.house.row,
            col: manualSummary.house.col,
          }))
          : escapeHtml(t("onboarding.house_manual_hint"))}</p>
        <div id="onb-house-controls" ${state.skipHouse ? 'style="display:none"' : ""}>
          <div class="onb-house-fields">
            <label class="onb-field"><span>${escapeHtml(t("onboarding.row"))}</span>
              <input type="number" id="onb-house-row" min="1"
                max="${state.gridRows}" value="${state.houseRow}" /></label>
            <label class="onb-field"><span>${escapeHtml(t("onboarding.column"))}</span>
              <input type="number" id="onb-house-col" min="1"
                max="${state.gridCols}" value="${state.houseCol}" /></label>
            <label class="onb-field"><span>${escapeHtml(t("onboarding.width"))}</span>
              <input type="number" id="onb-house-w" min="1"
                max="${state.gridCols}" value="${state.houseWidth}" /></label>
            <label class="onb-field"><span>${escapeHtml(t("onboarding.depth"))}</span>
              <input type="number" id="onb-house-h" min="1"
                max="${state.gridRows}" value="${state.houseHeight}" /></label>
          </div>
          <div class="onb-grid-preview" id="onb-house-preview"></div>
        </div>
      </div>
    `;
  }

  function renderLocation(): string {
    return `
      <div class="onb-location">
        <h2>${escapeHtml(t("onboarding.location_title"))}</h2>
        <p>${escapeHtml(t("onboarding.location_body", { settingsLabel: gardenSettingsLabel() }))}</p>
        <label class="onb-field">
          <span>${escapeHtml(t("onboarding.address"))}</span>
          <input type="text" id="onb-address"
            value="${escapeHtml(state.address)}"
            placeholder="${escapeHtml(t("onboarding.address_placeholder"))}"
            maxlength="500" />
        </label>
        <div class="onb-latlon">
          <label class="onb-field">
            <span>${escapeHtml(t("onboarding.latitude"))}</span>
            <input type="number" id="onb-lat" step="0.0001"
              min="-90" max="90"
              value="${state.latitude ?? ""}"
              placeholder="59.95" />
          </label>
          <label class="onb-field">
            <span>${escapeHtml(t("onboarding.longitude"))}</span>
            <input type="number" id="onb-lon" step="0.0001"
              min="-180" max="180"
              value="${state.longitude ?? ""}"
              placeholder="10.75" />
          </label>
        </div>
        <button type="button" id="onb-geolocate" class="onb-geo-btn">
          ${escapeHtml(t("onboarding.use_current_location"))}
        </button>
        <p class="onb-hint">${escapeHtml(t("onboarding.location_skip_hint"))}</p>
      </div>
    `;
  }

  function renderZones(): string {
    const manualSummary = summarizeManualSetup(state);
    return `
      <div class="onb-zones">
        <h2>${escapeHtml(t("onboarding.zones_title"))}</h2>
        <p>${escapeHtml(t("onboarding.zones_body"))}</p>
        <div id="onb-zone-list">
          ${state.zones.map((z, i) => `
            <div class="onb-zone-item" data-idx="${i}">
              <span class="onb-zone-swatch" style="background:${z.color}"></span>
              <strong>${escapeHtml(z.code)}</strong> &ndash; ${escapeHtml(z.name)}
              <span class="onb-zone-range">(${z.startCol},${z.startRow})&ndash;(${z.endCol},${z.endRow})</span>
              <button class="onb-zone-remove" data-idx="${i}">&times;</button>
            </div>
          `).join("")}
        </div>
        <details class="onb-zone-add" open>
          <summary>${escapeHtml(t("onboarding.add_zone"))}</summary>
          <div class="onb-zone-form">
            <div class="onb-zone-form-row">
              <label class="onb-field"><span>${escapeHtml(t("onboarding.code"))}</span>
                <input type="text" id="onb-zcode" maxlength="20" placeholder="B" /></label>
              <label class="onb-field"><span>${escapeHtml(t("onboarding.name"))}</span>
                <input type="text" id="onb-zname" maxlength="120" placeholder="${escapeHtml(t("zones.name_placeholder"))}" /></label>
              <label class="onb-field"><span>${escapeHtml(t("onboarding.color"))}</span>
                <select id="onb-zcolor">
                  ${ZONE_COLORS.map((c, i) => `<option value="${c}" ${i === 0 ? "selected" : ""}>${c}</option>`).join("")}
                </select>
              </label>
            </div>
            <div class="onb-zone-form-row">
              <label class="onb-field"><span>${escapeHtml(t("onboarding.from_col"))}</span>
                <input type="number" id="onb-zsc" min="1" max="${state.gridCols}" value="1" /></label>
              <label class="onb-field"><span>${escapeHtml(t("onboarding.from_row"))}</span>
                <input type="number" id="onb-zsr" min="1" max="${state.gridRows}" value="1" /></label>
              <label class="onb-field"><span>${escapeHtml(t("onboarding.to_col"))}</span>
                <input type="number" id="onb-zec" min="1" max="${state.gridCols}" value="${state.gridCols}" /></label>
              <label class="onb-field"><span>${escapeHtml(t("onboarding.to_row"))}</span>
                <input type="number" id="onb-zer" min="1" max="${state.gridRows}" value="3" /></label>
            </div>
            <button type="button" id="onb-zone-add-btn">${escapeHtml(t("onboarding.add_zone"))}</button>
          </div>
        </details>
        <div class="onb-grid-preview" id="onb-zone-preview"></div>
        <div class="onb-summary onb-summary--compact">
          <div class="onb-summary-item">
            <span class="onb-summary-label">${escapeHtml(t("onboarding.zone_groups"))}</span>
            <span>${manualSummary.zoneGroupCount}</span>
          </div>
          <div class="onb-summary-item">
            <span class="onb-summary-label">${escapeHtml(t("onboarding.requested_cells"))}</span>
            <span>${manualSummary.requestedPlots}</span>
          </div>
          <div class="onb-summary-item">
            <span class="onb-summary-label">${escapeHtml(t("onboarding.expected_plots"))}</span>
            <span>${manualSummary.effectivePlots}</span>
          </div>
          <div class="onb-summary-item">
            <span class="onb-summary-label">${escapeHtml(t("onboarding.skipped_cells"))}</span>
            <span>${manualSummary.skippedPlots}</span>
          </div>
        </div>
        ${renderIssueBox(t("onboarding.fix_before_finishing"), manualSummary.errors, "error")}
        ${renderIssueBox(t("onboarding.preview_notes"), manualSummary.warnings, "warning")}
        <p class="onb-hint">${escapeHtml(t("onboarding.zones_optional_hint"))}</p>
      </div>
    `;
  }

  function renderComplete(): string {
    const manualSummary = summarizeManualSetup(state);
    const totalPlots = manualSummary.effectivePlots;
    const zoneCount = manualSummary.zoneGroupCount;
    const gridRows = state.gridRows;
    const gridCols = state.gridCols;
    const houseLabel = `${manualSummary.house.width}m x ${manualSummary.house.height}m ${t("onboarding.row").toLowerCase()} ${manualSummary.house.row}, ${t("onboarding.column").toLowerCase()} ${manualSummary.house.col}${manualSummary.usesDefaultHouse ? ` (${t("onboarding.default_house")})` : ""}`;
    const locationLabel = state.address.trim()
      || (state.latitude != null && state.longitude != null
        ? `${state.latitude}, ${state.longitude}`
        : t("onboarding.not_set"));
    return `
      <div class="onb-complete">
        <h2>${escapeHtml(t("onboarding.ready_title"))}</h2>
        <p>${escapeHtml(t("onboarding.ready_body", { settingsLabel: gardenSettingsLabel() }))}</p>
        <div class="onb-summary">
          <div class="onb-summary-item">
            <span class="onb-summary-label">${escapeHtml(t("onboarding.summary_garden_name"))}</span>
            <span>${escapeHtml(state.gardenName)}</span>
          </div>
          <div class="onb-summary-item">
            <span class="onb-summary-label">${escapeHtml(t("onboarding.summary_grid_size"))}</span>
            <span>${escapeHtml(t("onboarding.summary_grid_value", { cols: gridCols, rows: gridRows, area: gridCols * gridRows }))}</span>
          </div>
          <div class="onb-summary-item">
            <span class="onb-summary-label">${escapeHtml(t("onboarding.summary_house"))}</span>
            <span>${houseLabel}</span>
          </div>
          <div class="onb-summary-item">
            <span class="onb-summary-label">${escapeHtml(t("onboarding.summary_location"))}</span>
            <span>${escapeHtml(locationLabel)}</span>
          </div>
          <div class="onb-summary-item">
            <span class="onb-summary-label">${escapeHtml(t("onboarding.summary_zones"))}</span>
            <span>${escapeHtml(t("onboarding.summary_zones_value", { zoneCount, totalPlots }))}</span>
          </div>
          ${manualSummary.skippedPlots > 0
            ? `<div class="onb-summary-item"><span class="onb-summary-label">${escapeHtml(t("onboarding.summary_skipped_cells"))}</span><span>${manualSummary.skippedPlots}</span></div>`
            : ""}
        </div>
        ${renderIssueBox(t("onboarding.fix_before_finishing"), manualSummary.errors, "error")}
        ${renderIssueBox(t("onboarding.preview_notes"), manualSummary.warnings, "warning")}

        <h3>${escapeHtml(t("onboarding.what_next"))}</h3>
        <div class="onb-intro-features">
          <div class="onb-intro-feature">
            <strong>${escapeHtml(t("onboarding.next_map_title"))}</strong>
            <span>${escapeHtml(t("onboarding.next_map_body"))}</span>
          </div>
          <div class="onb-intro-feature">
            <strong>${escapeHtml(t("onboarding.next_plants_title"))}</strong>
            <span>${escapeHtml(t("onboarding.next_plants_body"))}</span>
          </div>
          <div class="onb-intro-feature">
            <strong>${escapeHtml(t("onboarding.next_care_title"))}</strong>
            <span>${escapeHtml(t("onboarding.next_care_body"))}</span>
          </div>
          <div class="onb-intro-feature">
            <strong>${escapeHtml(t("onboarding.next_analysis_title"))}</strong>
            <span>${escapeHtml(t("onboarding.next_analysis_body"))}</span>
          </div>
          <div class="onb-intro-feature">
            <strong>${escapeHtml(t("onboarding.next_statistics_title"))}</strong>
            <span>${escapeHtml(t("onboarding.next_statistics_body"))}</span>
          </div>
          ${nextStepsSettingsCard()}
        </div>
      </div>
    `;
  }

  // ── Grid preview renderer ──
  function drawGridPreview(
    containerId: string,
    opts?: { house?: boolean; zones?: boolean },
  ): void {
    const SVG_NS = "http://www.w3.org/2000/svg";
    const container2 = el.querySelector<HTMLElement>(`#${containerId}`);
    if (!container2) return;
    const maxW = Math.min(container2.clientWidth || 400, 400);
    const cellSize = Math.max(3, Math.min(
      Math.floor(maxW / state.gridCols),
      Math.floor(260 / state.gridRows),
      20,
    ));
    const w = cellSize * state.gridCols;
    const h = cellSize * state.gridRows;

    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("width", String(w));
    svg.setAttribute("height", String(h));
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("class", "onb-svg");

    const background = document.createElementNS(SVG_NS, "rect");
    background.setAttribute("width", String(w));
    background.setAttribute("height", String(h));
    background.setAttribute("fill", "var(--color-bg-subtle, #f0f0e8)");
    background.setAttribute("rx", "2");
    svg.appendChild(background);

    for (let c = 1; c < state.gridCols; c++) {
      const line = document.createElementNS(SVG_NS, "line");
      line.setAttribute("x1", String(c * cellSize));
      line.setAttribute("y1", "0");
      line.setAttribute("x2", String(c * cellSize));
      line.setAttribute("y2", String(h));
      line.setAttribute("stroke", "var(--color-border, #ccc)");
      line.setAttribute("stroke-width", "0.5");
      svg.appendChild(line);
    }
    for (let r = 1; r < state.gridRows; r++) {
      const line = document.createElementNS(SVG_NS, "line");
      line.setAttribute("x1", "0");
      line.setAttribute("y1", String(r * cellSize));
      line.setAttribute("x2", String(w));
      line.setAttribute("y2", String(r * cellSize));
      line.setAttribute("stroke", "var(--color-border, #ccc)");
      line.setAttribute("stroke-width", "0.5");
      svg.appendChild(line);
    }
    if (opts?.zones) {
      for (const z of state.zones) {
        const x = (z.startCol - 1) * cellSize;
        const y = (z.startRow - 1) * cellSize;
        const zw = (z.endCol - z.startCol + 1) * cellSize;
        const zh = (z.endRow - z.startRow + 1) * cellSize;
        const zoneRect = document.createElementNS(SVG_NS, "rect");
        zoneRect.setAttribute("x", String(x));
        zoneRect.setAttribute("y", String(y));
        zoneRect.setAttribute("width", String(zw));
        zoneRect.setAttribute("height", String(zh));
        zoneRect.setAttribute("fill", z.color);
        zoneRect.setAttribute("fill-opacity", "0.35");
        zoneRect.setAttribute("stroke", z.color);
        zoneRect.setAttribute("stroke-width", "1.5");
        zoneRect.setAttribute("rx", "1");
        svg.appendChild(zoneRect);

        const zoneLabel = document.createElementNS(SVG_NS, "text");
        zoneLabel.setAttribute("x", String(x + 3));
        zoneLabel.setAttribute("y", String(y + cellSize - 2));
        zoneLabel.setAttribute("font-size", String(Math.max(8, cellSize - 2)));
        zoneLabel.setAttribute("fill", z.color);
        zoneLabel.setAttribute("font-weight", "600");
        zoneLabel.textContent = z.code;
        svg.appendChild(zoneLabel);
      }
    }
    if (opts?.house) {
      const previewHouse = getEffectiveHouseState(state).house;
      const hx = (previewHouse.col - 1) * cellSize;
      const hy = (previewHouse.row - 1) * cellSize;
      const hw = previewHouse.width * cellSize;
      const hh = previewHouse.height * cellSize;
      const houseRect = document.createElementNS(SVG_NS, "rect");
      houseRect.setAttribute("x", String(hx));
      houseRect.setAttribute("y", String(hy));
      houseRect.setAttribute("width", String(hw));
      houseRect.setAttribute("height", String(hh));
      houseRect.setAttribute("fill", "var(--color-house, #a0522d)");
      houseRect.setAttribute("fill-opacity", "0.6");
      houseRect.setAttribute("stroke", "var(--color-house, #a0522d)");
      houseRect.setAttribute("stroke-width", "2");
      houseRect.setAttribute("rx", "2");
      svg.appendChild(houseRect);

      const houseLabel = document.createElementNS(SVG_NS, "text");
      houseLabel.setAttribute("x", String(hx + hw / 2));
      houseLabel.setAttribute("y", String(hy + hh / 2 + 4));
      houseLabel.setAttribute("text-anchor", "middle");
      houseLabel.setAttribute("font-size", "10");
      houseLabel.setAttribute("fill", "white");
      houseLabel.setAttribute("font-weight", "600");
      houseLabel.textContent = t(state.skipHouse ? "onboarding.default_house" : "onboarding.house_label");
      svg.appendChild(houseLabel);
    }
    container2.replaceChildren(svg);
  }

  // ── Wire event handlers per step ──
  function wireStep(): void {
    el.querySelectorAll<HTMLButtonElement>("[data-locale-option]").forEach((button) => {
      button.addEventListener("click", () => {
        void updateLocale(button.dataset["localeOption"] === "no" ? "no" : "en");
      });
    });
    // Nav buttons.
    el.querySelector(".onb-back")?.addEventListener("click", () => {
      saveCurrentStep();
      state.step = Math.max(0, state.step - 1);
      render();
    });
    el.querySelector(".onb-next")?.addEventListener("click", () => {
      saveCurrentStep();
      state.step = Math.min(TOTAL_STEPS - 1, state.step + 1);
      render();
    });
    el.querySelector(".onb-finish")?.addEventListener("click", () => {
      void finishOnboarding();
    });
    el.querySelector(".onb-dismiss")?.addEventListener("click", () => {
      void dismissOnboarding();
    });

    // Step-specific wiring.
    switch (state.step) {
      case 1: wireNameIt(); break;
      case 2: wirePropertySize(); break;
      case 3: wireHousePlacement(); break;
      case 4: wireLocation(); break;
      case 5: wireZones(); break;
    }
  }

  // ── Step wiring ──

  function wireNameIt(): void {
    const input = el.querySelector<HTMLInputElement>("#onb-garden-name");
    input?.addEventListener("input", () => {
      state.gardenName = input.value.trim() || cbs.gardenName;
    });
  }

  function wirePropertySize(): void {
    const cols = el.querySelector<HTMLInputElement>("#onb-cols");
    const rows = el.querySelector<HTMLInputElement>("#onb-rows");
    const update = () => {
      const c = Number.parseInt(cols?.value ?? "22", 10);
      const r = Number.parseInt(rows?.value ?? "30", 10);
      if (c >= 5 && c <= 100) state.gridCols = c;
      if (r >= 5 && r <= 100) state.gridRows = r;
      const hint = el.querySelector(".onb-hint");
      if (hint) {
        hint.textContent = t("onboarding.total_area", {
          cols: state.gridCols,
          rows: state.gridRows,
          area: state.gridCols * state.gridRows,
        });
      }
      drawGridPreview("onb-grid-preview");
    };
    cols?.addEventListener("input", update);
    rows?.addEventListener("input", update);
    requestAnimationFrame(() => drawGridPreview("onb-grid-preview"));
  }

  function wireHousePlacement(): void {
    const skip = el.querySelector<HTMLInputElement>("#onb-skip-house");
    const controls = el.querySelector<HTMLElement>("#onb-house-controls");
    skip?.addEventListener("change", () => {
      state.skipHouse = skip.checked;
      if (controls) controls.style.display = skip.checked ? "none" : "";
      drawGridPreview("onb-house-preview", { house: true });
    });
    const fields = ["onb-house-row", "onb-house-col", "onb-house-w", "onb-house-h"] as const;
    const keys: (keyof OnboardingState)[] = ["houseRow", "houseCol", "houseWidth", "houseHeight"];
    for (let i = 0; i < fields.length; i++) {
      const input = el.querySelector<HTMLInputElement>(`#${fields[i]}`);
      const key = keys[i] as "houseRow" | "houseCol" | "houseWidth" | "houseHeight";
      input?.addEventListener("input", () => {
        const v = Number.parseInt(input.value, 10);
        if (Number.isFinite(v) && v >= 1) {
          state[key] = v;
          drawGridPreview("onb-house-preview", { house: true });
        }
      });
    }
    requestAnimationFrame(() => drawGridPreview("onb-house-preview", { house: true }));
  }

  function wireLocation(): void {
    const addr = el.querySelector<HTMLInputElement>("#onb-address");
    const lat = el.querySelector<HTMLInputElement>("#onb-lat");
    const lon = el.querySelector<HTMLInputElement>("#onb-lon");
    addr?.addEventListener("input", () => { state.address = addr.value; });
    lat?.addEventListener("input", () => {
      const v = Number.parseFloat(lat.value);
      state.latitude = Number.isFinite(v) ? v : null;
    });
    lon?.addEventListener("input", () => {
      const v = Number.parseFloat(lon.value);
      state.longitude = Number.isFinite(v) ? v : null;
    });
    el.querySelector("#onb-geolocate")?.addEventListener("click", () => {
      if (!navigator.geolocation) return;
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          state.latitude = Math.round(pos.coords.latitude * 10000) / 10000;
          state.longitude = Math.round(pos.coords.longitude * 10000) / 10000;
          if (lat) lat.value = String(state.latitude);
          if (lon) lon.value = String(state.longitude);
        },
        () => {
          const btn = el.querySelector<HTMLButtonElement>("#onb-geolocate");
          if (btn) btn.textContent = t("onboarding.location_denied");
        },
      );
    });
  }

  function wireZones(): void {
    el.querySelector("#onb-zone-add-btn")?.addEventListener("click", () => {
      const code = el.querySelector<HTMLInputElement>("#onb-zcode")?.value.trim() ?? "";
      const name = el.querySelector<HTMLInputElement>("#onb-zname")?.value.trim() ?? "";
      const zColor: string = el.querySelector<HTMLSelectElement>("#onb-zcolor")?.value ?? "#4a7c59";
      const sc = Number.parseInt(el.querySelector<HTMLInputElement>("#onb-zsc")?.value ?? "1", 10);
      const sr = Number.parseInt(el.querySelector<HTMLInputElement>("#onb-zsr")?.value ?? "1", 10);
      const ec = Number.parseInt(el.querySelector<HTMLInputElement>("#onb-zec")?.value ?? "1", 10);
      const er = Number.parseInt(el.querySelector<HTMLInputElement>("#onb-zer")?.value ?? "1", 10);
      if (!code || !name) return;
      if (sc > ec || sr > er) return;
      state.zones.push({
        code, name, color: zColor,
        startCol: sc, startRow: sr, endCol: ec, endRow: er,
      });
      render();
    });
    el.querySelectorAll<HTMLButtonElement>(".onb-zone-remove").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number.parseInt(btn.dataset["idx"] ?? "-1", 10);
        if (idx >= 0) {
          state.zones.splice(idx, 1);
          render();
        }
      });
    });
    requestAnimationFrame(() => drawGridPreview("onb-zone-preview", { house: true, zones: true }));
  }

  function saveCurrentStep(): void {
    switch (state.step) {
      case 1: {
        const v = el.querySelector<HTMLInputElement>("#onb-garden-name")?.value.trim();
        if (v) state.gardenName = v;
        break;
      }
      case 2: {
        const c = Number.parseInt(el.querySelector<HTMLInputElement>("#onb-cols")?.value ?? "", 10);
        const r = Number.parseInt(el.querySelector<HTMLInputElement>("#onb-rows")?.value ?? "", 10);
        if (c >= 5 && c <= 100) state.gridCols = c;
        if (r >= 5 && r <= 100) state.gridRows = r;
        break;
      }
      case 4: {
        state.address = el.querySelector<HTMLInputElement>("#onb-address")?.value ?? "";
        const lat = Number.parseFloat(el.querySelector<HTMLInputElement>("#onb-lat")?.value ?? "");
        const lon = Number.parseFloat(el.querySelector<HTMLInputElement>("#onb-lon")?.value ?? "");
        state.latitude = Number.isFinite(lat) ? lat : null;
        state.longitude = Number.isFinite(lon) ? lon : null;
        break;
      }
    }
  }

  async function finishOnboarding(): Promise<void> {
    const finishBtn = el.querySelector<HTMLButtonElement>(".onb-finish");
    if (finishBtn) {
      finishBtn.disabled = true;
      finishBtn.textContent = t("onboarding.saving");
    }
    try {
      if (!state.gardenName.trim()) {
        throw new Error(t("onboarding.garden_name_required"));
      }
      const manualSummary = summarizeManualSetup(state);
      if (manualSummary.errors.length > 0) {
        throw new Error(manualSummary.errors[0] ?? t("onboarding.manual_fix_required"));
      }
      const payload: {
        name: string;
        grid_rows: number;
        grid_cols: number;
        latitude: number | null;
        longitude: number | null;
        address: string;
        mode: "manual";
        house?: {
          row: number;
          col: number;
          width: number;
          height: number;
          north_degrees: number;
          grid_rows: number;
          grid_cols: number;
        };
        zones?: Array<{
          zone_code: string;
          zone_name: string;
          start_row: number;
          start_col: number;
          end_row: number;
          end_col: number;
          color: string;
        }>;
      } = {
        name: state.gardenName,
        grid_rows: state.gridRows,
        grid_cols: state.gridCols,
        latitude: state.latitude,
        longitude: state.longitude,
        address: state.address.trim(),
        mode: "manual",
      };
      payload.zones = state.zones.map((z) => ({
        zone_code: z.code,
        zone_name: z.name,
        start_row: z.startRow,
        start_col: z.startCol,
        end_row: z.endRow,
        end_col: z.endCol,
        color: z.color,
      }));
      if (!state.skipHouse) {
        payload.house = {
          row: state.houseRow,
          col: state.houseCol,
          width: state.houseWidth,
          height: state.houseHeight,
          north_degrees: 0,
          grid_rows: state.gridRows,
          grid_cols: state.gridCols,
        };
      }
      await completeGardenOnboardingApi(cbs.gardenId, payload);

      // Done!
      document.removeEventListener("keydown", blockEscape, true);
      stopLocaleSubscription();
      el.remove();
      cbs.onComplete();
    } catch (err) {
      if (finishBtn) {
        finishBtn.disabled = false;
        finishBtn.textContent = t("onboarding.create_garden");
      }
      const errMsg = err instanceof Error ? err.message : t("onboarding.error_failed_save");
      const errDiv = el.querySelector(".onb-error") ?? document.createElement("div");
      errDiv.className = "onb-error";
      errDiv.textContent = errMsg;
      el.querySelector(".onboarding-nav")?.prepend(errDiv);
    }
  }

  async function dismissOnboarding(): Promise<void> {
    const dismissBtn = el.querySelector<HTMLButtonElement>(".onb-dismiss");
    if (!dismissBtn) return;
    if (!window.confirm(t("onboarding.dismiss_confirm"))) {
      return;
    }
    dismissBtn.disabled = true;
    dismissBtn.textContent = t("onboarding.dismissing");
    try {
      await updateGardenSettingsApi(cbs.gardenId, {
        onboarding_complete: true,
      });
      document.removeEventListener("keydown", blockEscape, true);
      stopLocaleSubscription();
      el.remove();
      cbs.onDismiss?.();
    } catch (err) {
      dismissBtn.disabled = false;
      dismissBtn.textContent = t("onboarding.dismiss");
      const errMsg = err instanceof Error ? err.message : t("onboarding.error_failed_dismiss");
      const errDiv = el.querySelector(".onb-error") ?? document.createElement("div");
      errDiv.className = "onb-error";
      errDiv.textContent = errMsg;
      el.querySelector(".onboarding-nav")?.prepend(errDiv);
    }
  }

  async function updateLocale(nextLocale: "en" | "no"): Promise<void> {
    if (getLocale() === nextLocale) return;
    setLocale(nextLocale);
    try {
      await updateAuthMeSettingsApi({ language: nextLocale });
    } catch {
      // Keep the local choice even if persistence fails; finish-onboarding can continue.
    }
  }

  render();

  return {
    destroy: () => {
      document.removeEventListener("keydown", blockEscape, true);
      stopLocaleSubscription();
      el.remove();
    },
  };
}
