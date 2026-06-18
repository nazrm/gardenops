import { getLocale, t } from "../core/i18n";
import { escapeHtml } from "../core/sanitize";
import gardenOpsLogoUrl from "../assets/gardenops-logo-transparent.webp";

function getAnalysisStarters() {
  return [
    {
      starter: t("analysis.starter_1_prompt"),
      title: t("analysis.starter_1_title"),
      copy: t("analysis.starter_1_copy"),
    },
    {
      starter: t("analysis.starter_2_prompt"),
      title: t("analysis.starter_2_title"),
      copy: t("analysis.starter_2_copy"),
    },
    {
      starter: t("analysis.starter_3_prompt"),
      title: t("analysis.starter_3_title"),
      copy: t("analysis.starter_3_copy"),
    },
    {
      starter: t("analysis.starter_4_prompt"),
      title: t("analysis.starter_4_title"),
      copy: t("analysis.starter_4_copy"),
    },
  ] as const;
}

function getLocaleSwitchMarkup(scope: string): string {
  const locale = getLocale();
  const buttons = ([
    { value: "en", label: "EN" },
    { value: "no", label: "NO" },
  ] as const).map((item) => `
    <button
      type="button"
      class="locale-switch-btn${locale === item.value ? " active" : ""}"
      data-locale-option="${item.value}"
      data-locale-scope="${scope}"
      aria-pressed="${locale === item.value ? "true" : "false"}"
      lang="${item.value}"
    >${item.label}</button>
  `).join("");
  return `
    <div class="locale-switch" role="group" aria-label="${t("common.language")}">
      ${buttons}
    </div>
  `;
}

function getInsightsModeToggleMarkup(): string {
  return `
    <div class="insights-sub-header">
      <div class="insights-mode-toggle" role="tablist" aria-label="${t("nav.insights")}" data-i18n-aria-label="nav.insights">
        <button role="tab" aria-selected="false" data-sub-mode="care" data-i18n="nav.care">${t("nav.care")}</button>
        <button role="tab" aria-selected="false" data-sub-mode="statistics" data-i18n="nav.statistics">${t("nav.statistics")}</button>
        <button role="tab" aria-selected="false" data-sub-mode="analysis" data-i18n="nav.analysis">${t("nav.analysis")}</button>
      </div>
    </div>
  `;
}

function getAdminViewMarkup(): string {
  return `<section id="admin-view" class="view adm-view" role="tabpanel" aria-labelledby="top-tab-admin" hidden></section>`;
}

export function getAnalysisStartersMarkup(): string {
  const starters = getAnalysisStarters();
  return `
    <div id="analysis-starters" class="analysis-starters">
      <p class="starters-label">${t("analysis.starters_label")}</p>
      ${starters.map((item) => `
      <button class="starter-chip" data-starter="${item.starter}">
        <span class="starter-chip-title">${item.title}</span>
        <span class="starter-chip-copy">${item.copy}</span>
      </button>`).join("")}
    </div>
  `;
}

export function createAnalysisStartersElement(): HTMLDivElement {
  const starters = getAnalysisStarters();
  const container = document.createElement("div");
  container.id = "analysis-starters";
  container.className = "analysis-starters";

  const label = document.createElement("p");
  label.className = "starters-label";
  label.textContent = t("analysis.starters_label");
  container.appendChild(label);

  starters.forEach((item) => {
    const button = document.createElement("button");
    button.className = "starter-chip";
    button.dataset["starter"] = item.starter;
    button.type = "button";

    const title = document.createElement("span");
    title.className = "starter-chip-title";
    title.textContent = item.title;

    const copy = document.createElement("span");
    copy.className = "starter-chip-copy";
    copy.textContent = item.copy;

    button.append(title, copy);
    container.appendChild(button);
  });

  return container;
}

export function getAppShellMarkup(): string {
  const appTitle = escapeHtml(t("auth.app_title"));

  return `
    <div class="app-shell">
      <header class="mobile-header">
        <button class="mobile-header-logo-button" type="button" data-brand-home aria-label="${t("nav.map")}" title="${t("nav.map")}">
          <img
            class="mobile-header-logo"
            src="${gardenOpsLogoUrl}"
            alt="${appTitle}"
            width="640"
            height="427"
            decoding="async"
          />
        </button>
        <div class="mobile-header-main">
          <p class="mobile-header-kicker" id="mobile-garden-name">${t("nav.active_garden")}</p>
          <h1 class="mobile-header-title" id="mobile-view-title">${t("nav.map")}</h1>
        </div>
        <button
          id="mobile-utility-btn"
          class="mobile-icon-btn"
          type="button"
          aria-label="${t("nav.mobile_controls")}"
          aria-controls="mobile-utility-sheet"
          aria-expanded="false"
          data-i18n="nav.mobile_controls"
          data-i18n-aria-label="nav.mobile_controls"
        >
          ${t("nav.mobile_controls")}
        </button>
      </header>

      <header class="top-nav desktop-top-nav" role="tablist" aria-label="${t("nav.main_sections")}" data-i18n-aria-label="nav.main_sections">
        <div class="nav-tabs" id="nav-tabs">
          <button class="app-brand" type="button" data-brand-home aria-label="${t("nav.map")}" title="${t("nav.map")}">
            <img
              class="app-brand-logo"
              src="${gardenOpsLogoUrl}"
              alt="${appTitle}"
              width="640"
              height="427"
              decoding="async"
            />
            <span class="app-brand-name">${appTitle}</span>
          </button>
          <button id="top-tab-map" class="top-tab active" data-tab="map" role="tab" aria-selected="true" aria-controls="map-view" tabindex="0" data-i18n="nav.map">${t("nav.map")}</button>
          <button id="top-tab-garden" class="top-tab" data-tab="garden" role="tab" aria-selected="false" aria-controls="plants-view" tabindex="-1" data-i18n="nav.garden">${t("nav.garden")}</button>
          <button id="top-tab-activity" class="top-tab" data-tab="activity" role="tab" aria-selected="false" aria-controls="plants-view" tabindex="-1" data-i18n="nav.activity"><span data-tab-label>${t("nav.activity")}</span><span class="tab-badge" id="tab-badge-activity" hidden></span></button>
          <button id="top-tab-insights" class="top-tab" data-tab="insights" role="tab" aria-selected="false" aria-controls="care-view" tabindex="-1" data-i18n="nav.insights"><span data-tab-label>${t("nav.insights")}</span><span class="tab-badge" id="tab-badge-insights" hidden></span></button>
          <div class="top-search global-search-shell">
            <input
              id="global-plant-search"
              class="global-search-input"
              data-dropdown-id="global-search-dropdown"
              type="search"
              name="plant-search"
              placeholder="${t("nav.search_placeholder")}"
              data-i18n-placeholder="nav.search_placeholder"
              autocomplete="off"
            />
            <div id="global-search-dropdown" class="search-dropdown global-search-dropdown" aria-live="polite" hidden></div>
          </div>
          <span class="top-nav-spacer"></span>
          <div class="garden-switch">
            <select id="garden-select" class="garden-select" data-garden-select aria-label="${t("nav.active_garden")}" hidden></select>
            <button id="garden-create-btn" class="garden-create-btn" data-garden-create title="${t("nav.create_garden")}" data-i18n-title="nav.create_garden" hidden>+</button>
            <span id="garden-role-chip" class="garden-role-chip" data-garden-role hidden></span>
          </div>
          ${getLocaleSwitchMarkup("desktop")}
          <button id="top-tab-admin" class="top-tab top-tab--right" data-tab="admin" role="tab" aria-selected="false" aria-controls="admin-view" tabindex="-1">${t("nav.settings_user")}</button>
          <button id="auth-btn" class="auth-btn" data-auth-trigger title="${t("nav.sign_in")}">${t("nav.sign_in")}</button>
          <span class="notification-bell-wrapper">
            <button id="notification-bell" class="notification-bell" type="button" aria-label="${t("notifications.bell_label")}" title="${t("notifications.bell_label")}">
              <span class="notification-bell-icon">\uD83D\uDD14</span>
              <span id="notification-badge" class="notification-badge" hidden>0</span>
            </button>
          </span>
          <button id="theme-toggle" class="theme-toggle" data-theme-toggle aria-label="${t("nav.toggle_theme")}" title="${t("nav.toggle_theme")}"></button>
          <span class="top-meta app-clock" id="top-clock"></span>
        </div>
      </header>

      <div id="notification-panel" class="notification-panel" hidden></div>

      <main class="content-shell">
        <div id="app-status" class="app-status" hidden>
          <span id="app-status-text"></span>
          <div class="app-status-actions">
            <button id="app-status-action" hidden></button>
            <button id="app-status-dismiss" aria-label="${t("common.close")}" data-i18n-aria-label="common.close" hidden>&times;</button>
          </div>
        </div>
        <section id="map-view" class="view view-map active" role="tabpanel" aria-labelledby="top-tab-map">
          <div class="map-shell">
            <aside id="map-layers-panel" class="map-layers-panel" aria-labelledby="map-layers-title">
              <div class="map-layers-header">
                <div>
                  <p class="map-layers-kicker" data-i18n="map.map_controls">${t("map.map_controls")}</p>
                  <h2 id="map-layers-title" data-i18n="map.layers">${t("map.layers")}</h2>
                </div>
                <button
                  id="map-layers-collapse-btn"
                  class="map-layers-collapse-btn"
                  type="button"
                  aria-label="${t("map.collapse_layers")}"
                  title="${t("map.collapse_layers")}"
                  aria-controls="map-layers-panel"
                  aria-expanded="true"
                >&#8249;</button>
                <button id="mobile-map-layers-close-btn" class="map-layers-close close-btn" type="button" aria-label="${t("map.close_layers")}" data-i18n-aria-label="map.close_layers">&times;</button>
              </div>

              <details id="map-layer-zones-section" class="map-layer-section map-layer-disclosure">
                <summary class="map-layer-section-header">
                  <h3 id="map-layer-zones-title" data-i18n="map.zones">${t("map.zones")}</h3>
                </summary>
                <div class="zone-toggles" id="zone-toggles"></div>
              </details>

              <section id="map-layer-highlight-section" class="map-layer-section" aria-labelledby="map-layer-highlight-title">
                <div class="map-layer-section-header">
                  <h3 id="map-layer-highlight-title" data-i18n="map.highlight_layer">${t("map.highlight_layer")}</h3>
                </div>
                <div class="category-filters" id="category-filters">
                  <button class="cat-filter-btn" data-cat="løk" title="${t("category.lok")}" data-i18n-title="category.lok">🧅 <span data-i18n="category.lok">${t("category.lok")}</span></button>
                  <button class="cat-filter-btn" data-cat="frø" title="${t("category.fro")}" data-i18n-title="category.fro">🌱 <span data-i18n="category.fro">${t("category.fro")}</span></button>
                  <button class="cat-filter-btn" data-cat="busker" title="${t("category.busker")}" data-i18n-title="category.busker">🌿 <span data-i18n="category.busker">${t("category.busker")}</span></button>
                  <button class="cat-filter-btn" data-cat="baerbusker" title="${t("category.baerbusker")}" data-i18n-title="category.baerbusker">🍓 <span data-i18n="category.baerbusker">${t("category.baerbusker")}</span></button>
                  <button class="cat-filter-btn" data-cat="trær" title="${t("category.traer")}" data-i18n-title="category.traer">🌳 <span data-i18n="category.traer">${t("category.traer")}</span></button>
                </div>
              </section>

              <section id="map-layer-elevation-section" class="map-layer-section" aria-labelledby="map-layer-elevation-title" hidden>
                <div class="map-layer-section-header">
                  <h3 id="map-layer-elevation-title" data-i18n="map.elevation">${t("map.elevation")}</h3>
                </div>
                <div class="map-layer-actions">
                  <button id="elevation-toggle-btn" class="cat-filter-btn" title="${t("map.elevation_toggle_title")}" data-i18n="map.elevation" data-i18n-title="map.elevation_toggle_title">${t("map.elevation")}</button>
                  <button id="elevation-edit-btn" class="cat-filter-btn elev-edit-btn" title="${t("map.elevation_edit_title")}" data-i18n-title="map.elevation_edit_title" hidden>&#9998;</button>
                </div>
              </section>
            </aside>

            <div class="map-workspace">
              <input id="import-map-input" type="file" accept=".json" hidden />
              <div id="map-edit-context-bar" class="toolbar map-edit-context-bar" hidden>
                <button id="select-all-btn" style="display:none;" data-i18n="map.select_all">${t("map.select_all")}</button>
                <button id="clear-selection-btn" style="display:none;" data-i18n="map.clear_selection">${t("map.clear_selection")}</button>
                <button id="undo-btn" style="display:none;" disabled data-i18n="common.undo">${t("common.undo")}</button>
                <span id="selection-count" style="display:none;"></span>
              </div>

              <div class="mobile-map-actionbar" role="toolbar" aria-label="${t("map.map_controls")}" data-i18n-aria-label="map.map_controls">
                <button
                  id="mobile-map-layers-btn"
                  class="mobile-map-action"
                  type="button"
                  aria-controls="map-layers-panel"
                  aria-expanded="false"
                >
                  <span data-i18n="map.layers">${t("map.layers")}</span>
                </button>
                <button
                  id="mobile-map-highlight-btn"
                  class="mobile-map-action"
                  type="button"
                  aria-controls="map-layers-panel"
                  aria-expanded="false"
                >
                  <span data-i18n="map.highlight_layer">${t("map.highlight_layer")}</span>
                </button>
                <button
                  id="mobile-map-shade-btn"
                  class="mobile-map-action"
                  type="button"
                  aria-controls="shade-panel"
                  aria-expanded="false"
                >
                  <span data-i18n="map.shade">${t("map.shade")}</span>
                </button>
              </div>

              <div id="map-status-slot" class="map-status-slot" aria-live="polite"></div>

              <div class="map-layout">
            <div class="map-stage">
              <div class="map-viewport" id="map-viewport">
                <div class="map-camera" id="map-camera">
                  <div class="map-edge-label map-edge-top" id="map-edge-top" aria-live="polite"></div>
                  <div class="map-edge-label map-edge-right" id="map-edge-right" aria-live="polite"></div>
                  <div class="map-edge-label map-edge-bottom" id="map-edge-bottom" aria-live="polite"></div>
                  <div class="map-edge-label map-edge-left" id="map-edge-left" aria-live="polite"></div>
                  <div class="map-grid" id="map-grid"></div>
                </div>
              </div>
            </div>
            <aside class="shade-panel" id="shade-panel" data-state="loading">
              <div class="shade-panel-header">
                <div>
                  <p class="shade-kicker" data-i18n="shade.kicker">${t("shade.kicker")}</p>
                  <h3 data-i18n="shade.title">${t("shade.title")}</h3>
                </div>
                <button id="mobile-map-shade-close-btn" class="shade-panel-close close-btn" type="button" aria-label="${t("map.close_shade")}" data-i18n-aria-label="map.close_shade">&times;</button>
              </div>
              <div class="shade-overview-card">
                <div class="shade-overview-copy">
                  <p id="shade-summary" class="shade-summary">${t("shade.loading_config")}</p>
                  <div class="shade-overview-meta">
                    <label id="shade-target-field" class="shade-field shade-target-field" for="shade-target-select" hidden>
                      <span data-i18n="shade.target">${t("shade.target")}</span>
                      <select id="shade-target-select" aria-label="${t("shade.target_aria")}" data-i18n-aria-label="shade.target_aria" hidden>
                        <option value="house" data-i18n="shade.house_label">${t("shade.house_label")}</option>
                      </select>
                    </label>
                    <a id="shade-open-link" class="shade-open-link" href="#" target="_blank" rel="noreferrer noopener" hidden data-i18n="shade.open_link">${t("shade.open_link")}</a>
                  </div>
                </div>
                <div class="shade-map-wrap">
                  <div id="shade-map" class="shade-map" aria-label="${t("shade.map_aria")}" data-i18n-aria-label="shade.map_aria"></div>
                </div>
              </div>
              <div class="shade-controls">
                <div class="shade-time-group">
                  <select id="shade-preset-select" aria-label="${t("shade.preset_aria")}" data-i18n-aria-label="shade.preset_aria">
                    <option value="now" data-i18n="shade.preset_now">${t("shade.preset_now")}</option>
                    <option value="custom" hidden data-i18n="shade.preset_custom">${t("shade.preset_custom")}</option>
                    <option value="spring" data-i18n="shade.preset_spring">${t("shade.preset_spring")}</option>
                    <option value="summer" data-i18n="shade.preset_summer">${t("shade.preset_summer")}</option>
                    <option value="autumn" data-i18n="shade.preset_autumn">${t("shade.preset_autumn")}</option>
                    <option value="winter" data-i18n="shade.preset_winter">${t("shade.preset_winter")}</option>
                  </select>
                  <input id="shade-date-input" type="date" aria-label="${t("shade.date_aria")}" data-i18n-aria-label="shade.date_aria" lang="en-GB" />
                  <input id="shade-time-input" type="time" aria-label="${t("shade.time_aria")}" data-i18n-aria-label="shade.time_aria" lang="en-GB" step="60" />
                </div>
                <details class="shade-disclosure shade-disclosure-inline">
                  <summary data-i18n="shade.playback">${t("shade.playback")}</summary>
                  <div class="shade-playback-group">
                    <button id="shade-playback-btn" type="button" aria-pressed="false">${t("shade.play")}</button>
                    <label class="shade-field" for="shade-step-select">
                      <span data-i18n="shade.step">${t("shade.step")}</span>
                      <select id="shade-step-select" aria-label="${t("shade.step_aria")}" data-i18n-aria-label="shade.step_aria">
                        <option value="10">10 min</option>
                        <option value="15" selected>15 min</option>
                        <option value="30">30 min</option>
                      </select>
                    </label>
                    <label class="shade-field" for="shade-speed-select">
                      <span data-i18n="shade.speed">${t("shade.speed")}</span>
                      <select id="shade-speed-select" aria-label="${t("shade.speed_aria")}" data-i18n-aria-label="shade.speed_aria">
                        <option value="900" data-i18n="shade.speed_slow">${t("shade.speed_slow")}</option>
                        <option value="650" selected data-i18n="shade.speed_medium">${t("shade.speed_medium")}</option>
                        <option value="350" data-i18n="shade.speed_fast">${t("shade.speed_fast")}</option>
                      </select>
                    </label>
                  </div>
                </details>
              </div>
              <p id="shade-debug" class="shade-debug" hidden></p>
              <details class="shade-disclosure">
                <summary data-i18n="shade.monthly_title">${t("shade.monthly_title")}</summary>
                <div class="shade-comparison">
                  <span id="shade-comparison-status" class="shade-comparison-status">${t("shade.comparison_loading")}</span>
                  <div id="shade-comparison-list" class="shade-comparison-list"></div>
                </div>
              </details>
              <details class="shade-disclosure">
                <summary data-i18n="shade.calibration_title">${t("shade.calibration_title")}</summary>
                <div class="shade-settings-card">
                  <span id="shade-calibration-status" class="shade-comparison-status">${t("shade.calibration_fallback")}</span>
                  <p class="shade-note" data-i18n="shade.calibration_note">${t("shade.calibration_note")}</p>
                <div class="shade-calibration-grid">
                  <label class="shade-field" for="shade-cal-house-nw-lat">
                    <span data-i18n="shade.calibration_house_nw_lat">${t("shade.calibration_house_nw_lat")}</span>
                    <input id="shade-cal-house-nw-lat" type="number" step="0.000001" inputmode="decimal" />
                  </label>
                  <label class="shade-field" for="shade-cal-house-nw-lng">
                    <span data-i18n="shade.calibration_house_nw_lng">${t("shade.calibration_house_nw_lng")}</span>
                    <input id="shade-cal-house-nw-lng" type="number" step="0.000001" inputmode="decimal" />
                  </label>
                  <label class="shade-field" for="shade-cal-house-ne-lat">
                    <span data-i18n="shade.calibration_house_ne_lat">${t("shade.calibration_house_ne_lat")}</span>
                    <input id="shade-cal-house-ne-lat" type="number" step="0.000001" inputmode="decimal" />
                  </label>
                  <label class="shade-field" for="shade-cal-house-ne-lng">
                    <span data-i18n="shade.calibration_house_ne_lng">${t("shade.calibration_house_ne_lng")}</span>
                    <input id="shade-cal-house-ne-lng" type="number" step="0.000001" inputmode="decimal" />
                  </label>
                  <label class="shade-field" for="shade-cal-house-se-lat">
                    <span data-i18n="shade.calibration_house_se_lat">${t("shade.calibration_house_se_lat")}</span>
                    <input id="shade-cal-house-se-lat" type="number" step="0.000001" inputmode="decimal" />
                  </label>
                  <label class="shade-field" for="shade-cal-house-se-lng">
                    <span data-i18n="shade.calibration_house_se_lng">${t("shade.calibration_house_se_lng")}</span>
                    <input id="shade-cal-house-se-lng" type="number" step="0.000001" inputmode="decimal" />
                  </label>
                  <label class="shade-field" for="shade-cal-house-sw-lat">
                    <span data-i18n="shade.calibration_house_sw_lat">${t("shade.calibration_house_sw_lat")}</span>
                    <input id="shade-cal-house-sw-lat" type="number" step="0.000001" inputmode="decimal" />
                  </label>
                  <label class="shade-field" for="shade-cal-house-sw-lng">
                    <span data-i18n="shade.calibration_house_sw_lng">${t("shade.calibration_house_sw_lng")}</span>
                    <input id="shade-cal-house-sw-lng" type="number" step="0.000001" inputmode="decimal" />
                  </label>
                </div>
                <div id="shade-calibration-qa" class="shade-calibration-qa">
                  <div class="shade-calibration-qa-metrics">
                    <span id="shade-calibration-rms">${t("shade.calibration_rms_na")}</span>
                    <span id="shade-calibration-max">${t("shade.calibration_max_na")}</span>
                  </div>
                  <div id="shade-calibration-overlay" class="shade-calibration-overlay">
                    <p class="shade-comparison-empty">${t("shade.calibration_empty")}</p>
                  </div>
                </div>
                <div class="shade-editor-actions">
                  <button id="shade-calibration-fill-btn" type="button" data-i18n="shade.calibration_fill">${t("shade.calibration_fill")}</button>
                  <button id="shade-calibration-save-btn" type="button" data-i18n="shade.calibration_save">${t("shade.calibration_save")}</button>
                  <button id="shade-calibration-reset-btn" type="button" data-i18n="shade.calibration_reset">${t("shade.calibration_reset")}</button>
                </div>
                </div>
              </details>
              <details class="shade-disclosure">
                <summary data-i18n="shade.obstacles_title">${t("shade.obstacles_title")}</summary>
                <div class="shade-settings-card">
                  <span id="shade-obstacle-status" class="shade-comparison-status">${t("shade.obstacle_status_new")}</span>
                <label class="shade-field" for="shade-obstacle-select">
                  <span data-i18n="shade.obstacle_select">${t("shade.obstacle_select")}</span>
                  <select id="shade-obstacle-select" aria-label="${t("shade.obstacle_select_aria")}" data-i18n-aria-label="shade.obstacle_select_aria">
                    <option value="new" data-i18n="shade.obstacle_new">${t("shade.obstacle_new")}</option>
                  </select>
                </label>
                <div class="shade-calibration-grid">
                  <label class="shade-field" for="shade-obstacle-label">
                    <span data-i18n="shade.obstacle_label">${t("shade.obstacle_label")}</span>
                    <input id="shade-obstacle-label" type="text" maxlength="120" />
                  </label>
                  <label class="shade-field" for="shade-obstacle-kind">
                    <span data-i18n="shade.obstacle_kind">${t("shade.obstacle_kind")}</span>
                    <select id="shade-obstacle-kind">
                      <option value="tree" data-i18n="shade.obstacle_kind_tree">${t("shade.obstacle_kind_tree")}</option>
                      <option value="structure" data-i18n="shade.obstacle_kind_structure">${t("shade.obstacle_kind_structure")}</option>
                    </select>
                  </label>
                  <label class="shade-field" for="shade-obstacle-plot">
                    <span data-i18n="shade.obstacle_plot">${t("shade.obstacle_plot")}</span>
                    <select id="shade-obstacle-plot">
                      <option value="" data-i18n="shade.obstacle_no_plot">${t("shade.obstacle_no_plot")}</option>
                    </select>
                  </label>
                  <label class="shade-field" for="shade-obstacle-height">
                    <span data-i18n="shade.obstacle_height">${t("shade.obstacle_height")}</span>
                    <input id="shade-obstacle-height" type="number" step="0.1" min="0.1" inputmode="decimal" />
                  </label>
                  <label class="shade-field" for="shade-obstacle-radius">
                    <span data-i18n="shade.obstacle_radius">${t("shade.obstacle_radius")}</span>
                    <input id="shade-obstacle-radius" type="number" step="0.1" min="0.1" inputmode="decimal" />
                  </label>
                  <label class="shade-field" for="shade-obstacle-lat">
                    <span data-i18n="shade.obstacle_latitude">${t("shade.obstacle_latitude")}</span>
                    <input id="shade-obstacle-lat" type="number" step="0.000001" inputmode="decimal" />
                  </label>
                  <label class="shade-field" for="shade-obstacle-lng">
                    <span data-i18n="shade.obstacle_longitude">${t("shade.obstacle_longitude")}</span>
                    <input id="shade-obstacle-lng" type="number" step="0.000001" inputmode="decimal" />
                  </label>
                  <label class="shade-field shade-checkbox-field" for="shade-obstacle-active">
                    <span data-i18n="shade.obstacle_active">${t("shade.obstacle_active")}</span>
                    <input id="shade-obstacle-active" type="checkbox" checked />
                  </label>
                </div>
                <div class="shade-editor-actions">
                  <button id="shade-obstacle-fill-target-btn" type="button" data-i18n="shade.obstacle_fill_target">${t("shade.obstacle_fill_target")}</button>
                  <button id="shade-obstacle-save-btn" type="button" data-i18n="shade.obstacle_save">${t("shade.obstacle_save")}</button>
                  <button id="shade-obstacle-delete-btn" type="button" data-i18n="shade.obstacle_delete">${t("shade.obstacle_delete")}</button>
                </div>
                </div>
              </details>
            </aside>
          </div>
            </div>
          </div>
          <div id="shade-mobile-backdrop" class="shade-mobile-backdrop" aria-hidden="true"></div>
        </section>

        <section id="plants-view" class="view" role="tabpanel" aria-labelledby="top-tab-garden top-tab-activity" hidden>
          <div class="plants-sub-header">
            <div class="plants-mode-groups">
              <div class="plants-mode-toggle" data-parent-tab-group="garden" role="tablist" aria-label="${t("nav.garden")}" data-i18n-aria-label="nav.garden">
                <button role="tab" aria-selected="true" data-sub-mode="plants" id="sub-mode-plants" data-i18n="plants.mode_plants">${t("plants.mode_plants")}</button>
                <button role="tab" aria-selected="false" data-sub-mode="inventory" id="sub-mode-inventory" data-i18n="plants.mode_inventory">${t("plants.mode_inventory")}</button>
                <button role="tab" aria-selected="false" data-sub-mode="indoor" id="sub-mode-indoor" data-i18n="plants.mode_indoor">${t("plants.mode_indoor")}</button>
                <button role="tab" aria-selected="false" data-sub-mode="procurement" id="sub-mode-procurement" data-i18n="plants.mode_procurement">${t("plants.mode_procurement")}</button>
              </div>
              <div class="plants-mode-toggle" data-parent-tab-group="activity" role="tablist" aria-label="${t("nav.activity")}" data-i18n-aria-label="nav.activity" hidden>
                <button role="tab" aria-selected="false" data-sub-mode="tasks" id="sub-mode-tasks" data-i18n="plants.mode_tasks">${t("plants.mode_tasks")}</button>
                <button role="tab" aria-selected="false" data-sub-mode="calendar" id="sub-mode-calendar" data-i18n="plants.mode_calendar">${t("plants.mode_calendar")}</button>
                <button role="tab" aria-selected="false" data-sub-mode="journal" id="sub-mode-journal" data-i18n="plants.mode_journal">${t("plants.mode_journal")}</button>
                <button role="tab" aria-selected="false" data-sub-mode="issues" id="sub-mode-issues" data-i18n="plants.mode_issues">${t("plants.mode_issues")}</button>
                <button role="tab" aria-selected="false" data-sub-mode="harvest" id="sub-mode-harvest" data-i18n="plants.mode_harvest">${t("plants.mode_harvest")}</button>
              </div>
            </div>
            <div class="saved-views-shell">
              <button type="button" id="saved-views-trigger" class="saved-views-btn" title="${t("saved_views.button")}" data-i18n-title="saved_views.button">${t("saved_views.button")}</button>
              <div id="saved-views-dropdown" class="saved-views-dropdown" hidden></div>
            </div>
          </div>
          <div id="batch-bar" class="batch-bar" hidden aria-live="polite">
            <span id="batch-count" class="batch-count"></span>
            <div class="batch-actions">
              <button type="button" data-batch="journal" class="batch-btn" data-i18n="plants.batch_log_event">${t("plants.batch_log_event")}</button>
              <button type="button" data-batch="year-planted" class="batch-btn" data-i18n="plants.batch_set_year">${t("plants.batch_set_year")}</button>
              <button type="button" data-batch="category" class="batch-btn" data-i18n="plants.batch_set_category">${t("plants.batch_set_category")}</button>
              <button type="button" data-batch="deer-resistant" class="batch-btn" data-i18n="plants.batch_deer_resistant">${t("plants.batch_deer_resistant")}</button>
              <button type="button" data-batch="assign-plots" class="batch-btn" data-i18n="plants.batch_assign_plots">${t("plants.batch_assign_plots")}</button>
              <button type="button" data-batch="remove-plots" class="batch-btn" data-i18n="plants.batch_remove_plots">${t("plants.batch_remove_plots")}</button>
              <button type="button" data-batch="care-note" class="batch-btn" data-i18n="plants.batch_append_care_note">${t("plants.batch_append_care_note")}</button>
              <button type="button" data-batch="clear" class="batch-btn batch-btn-clear" data-i18n="plants.batch_clear">${t("plants.batch_clear")}</button>
            </div>
          </div>
          <div id="plants-tab-content">
          <div class="data-view-header">
            <div class="data-view-title-row">
              <div class="data-view-title-block">
                <h2 data-i18n="plants.title">${t("plants.title")}</h2>
                <p id="plants-summary" class="plants-summary">${t("plants.summary", { unique: 0, total: 0 })}</p>
              </div>
              <div class="title-actions">
                <div class="col-toggle-group desktop-table-only">
                  <button
                    id="col-toggle-btn"
                    title="${t("plants.show_hide_columns")}"
                    data-i18n="plants.columns"
                    data-i18n-title="plants.show_hide_columns"
                  >${t("plants.columns")}</button>
                  <div id="col-toggle-dropdown" class="col-toggle-dropdown" hidden></div>
                </div>
                <button id="import-csv-btn">${t("plants.import_csv")}</button>
                <input id="import-csv-input" type="file" accept=".csv,text/csv" hidden />
                <button id="export-csv-btn">${t("plants.export_csv")}</button>
                <button id="add-plant-btn" class="btn-primary" data-i18n="plants.add_plant">${t("plants.add_plant")}</button>
              </div>
            </div>
            <div id="plants-export-bar"></div>
            <div class="filter-row">
              <input id="plants-search" type="text" placeholder="${t("plants.search_placeholder")}" data-i18n-placeholder="plants.search_placeholder" />
              <select id="plants-category">
                <option value="" data-i18n="category.all">${t("category.all")}</option>
                <option value="løk" data-i18n="category.lok">${t("category.lok")}</option>
                <option value="frø" data-i18n="category.fro">${t("category.fro")}</option>
                <option value="busker" data-i18n="category.busker">${t("category.busker")}</option>
                <option value="baerbusker" data-i18n="category.baerbusker">${t("category.baerbusker")}</option>
                <option value="trær" data-i18n="category.traer">${t("category.traer")}</option>
              </select>
              <select id="plants-presence-filter" aria-label="${t("plants.presence_filter_label")}" data-i18n-aria-label="plants.presence_filter_label">
                <option value="all" data-i18n="plants.presence_filter_all">${t("plants.presence_filter_all")}</option>
                <option value="current" data-i18n="plants.presence_filter_current">${t("plants.presence_filter_current")}</option>
                <option value="gone" data-i18n="plants.presence_filter_gone">${t("plants.presence_filter_gone")}</option>
                <option value="unobserved" data-i18n="plants.presence_filter_unobserved">${t("plants.presence_filter_unobserved")}</option>
              </select>
              <div class="mobile-sort-controls">
                <select id="plants-sort-field" aria-label="${t("plants.sort_by")}" data-i18n-aria-label="plants.sort_by">
                  <option value="name" data-i18n="plants.sort_name">${t("plants.sort_name")}</option>
                  <option value="latin" data-i18n="plants.sort_latin">${t("plants.sort_latin")}</option>
                  <option value="bloom_month" data-i18n="plants.sort_bloom">${t("plants.sort_bloom")}</option>
                  <option value="hardiness" data-i18n="plants.sort_hardiness">${t("plants.sort_hardiness")}</option>
                  <option value="height_cm" data-i18n="plants.sort_height">${t("plants.sort_height")}</option>
                  <option value="light" data-i18n="plants.sort_light">${t("plants.sort_light")}</option>
                  <option value="plot_ids" data-i18n="plants.sort_plots">${t("plants.sort_plots")}</option>
                  <option value="year_planted" data-i18n="plants.sort_year">${t("plants.sort_year")}</option>
                </select>
                <button id="plants-sort-dir" class="sort-dir-btn" type="button" aria-label="${t("plants.sort_toggle_current", { direction: "asc" })}">${t("common.asc")}</button>
              </div>
            </div>
          </div>
          <div id="plants-mobile-list" class="mobile-data-list" aria-live="polite"></div>
          <div class="table-wrap desktop-data-table-wrap">
            <table class="data-table">
              <thead id="plants-table-head"></thead>
              <tbody id="plants-table-body"></tbody>
            </table>
          </div>
          </div>
          <div id="journal-tab-content" hidden>
            <div class="data-view-header">
              <div class="data-view-title-row">
                <div class="data-view-title-block">
                  <h2 data-i18n="journal.title">${t("journal.title")}</h2>
                  <p id="journal-summary" class="plants-summary"></p>
                </div>
                <div class="title-actions">
                  <button id="journal-add-btn" class="btn-primary journal-add-btn" data-i18n="journal.add_entry">${t("journal.add_entry")}</button>
                </div>
              </div>
              <div class="journal-filters">
                <select id="journal-filter-type" aria-label="${t("journal.filter_event_type")}" data-i18n-aria-label="journal.filter_event_type">
                  <option value="" data-i18n="journal.filter_all_events">${t("journal.filter_all_events")}</option>
                  <option value="planted" data-i18n="journal.event.planted">${t("journal.event.planted")}</option>
                  <option value="moved" data-i18n="journal.event.moved">${t("journal.event.moved")}</option>
                  <option value="divided" data-i18n="journal.event.divided">${t("journal.event.divided")}</option>
                  <option value="pruned" data-i18n="journal.event.pruned">${t("journal.event.pruned")}</option>
                  <option value="watered" data-i18n="journal.event.watered">${t("journal.event.watered")}</option>
                  <option value="fertilized" data-i18n="journal.event.fertilized">${t("journal.event.fertilized")}</option>
                  <option value="bloomed" data-i18n="journal.event.bloomed">${t("journal.event.bloomed")}</option>
                  <option value="harvested" data-i18n="journal.event.harvested">${t("journal.event.harvested")}</option>
                  <option value="died" data-i18n="journal.event.died">${t("journal.event.died")}</option>
                  <option value="observed" data-i18n="journal.event.observed">${t("journal.event.observed")}</option>
                </select>
                <input id="journal-filter-search" type="text" placeholder="${t("journal.search_placeholder")}" data-i18n-placeholder="journal.search_placeholder" />
                <input id="journal-filter-actor" type="text" placeholder="${t("journal.actor_placeholder")}" data-i18n-placeholder="journal.actor_placeholder" />
                <input id="journal-filter-from" type="date" aria-label="${t("journal.filter_from")}" data-i18n-aria-label="journal.filter_from" />
                <input id="journal-filter-to" type="date" aria-label="${t("journal.filter_to")}" data-i18n-aria-label="journal.filter_to" />
              </div>
            </div>
            <div id="journal-export-bar"></div>
            <div id="journal-list" aria-live="polite"></div>
            <div id="journal-pagination" class="journal-pagination"></div>
          </div>
          <div id="inventory-tab-content" hidden>
            <div class="data-view-header">
              <div class="data-view-title-row">
                <div class="data-view-title-block">
                  <h2 data-i18n="inventory.title">${t("inventory.title")}</h2>
                  <p id="inventory-summary" class="plants-summary"></p>
                </div>
                <div class="title-actions">
                  <button id="inventory-add-btn" class="btn-primary" data-i18n="inventory.add_item">${t("inventory.add_item")}</button>
                </div>
              </div>
              <div class="filter-row">
                <input id="inventory-search" type="text" placeholder="${t("inventory.search_placeholder")}" data-i18n-placeholder="inventory.search_placeholder" />
                <select id="inventory-type-filter" aria-label="${t("inventory.filter_type")}" data-i18n-aria-label="inventory.filter_type">
                  <option value="" data-i18n="inventory.type.all">${t("inventory.type.all")}</option>
                  <option value="seed" data-i18n="inventory.type.seed">${t("inventory.type.seed")}</option>
                  <option value="bulb" data-i18n="inventory.type.bulb">${t("inventory.type.bulb")}</option>
                  <option value="tuber" data-i18n="inventory.type.tuber">${t("inventory.type.tuber")}</option>
                  <option value="division" data-i18n="inventory.type.division">${t("inventory.type.division")}</option>
                  <option value="bare_root" data-i18n="inventory.type.bare_root">${t("inventory.type.bare_root")}</option>
                  <option value="nursery" data-i18n="inventory.type.nursery">${t("inventory.type.nursery")}</option>
                  <option value="cutting" data-i18n="inventory.type.cutting">${t("inventory.type.cutting")}</option>
                  <option value="other" data-i18n="inventory.type.other">${t("inventory.type.other")}</option>
                </select>
              </div>
            </div>
            <div id="inventory-export-bar"></div>
            <div id="inventory-mobile-list" class="mobile-data-list" aria-live="polite"></div>
            <div class="table-wrap desktop-data-table-wrap">
              <table class="data-table">
                <thead id="inventory-table-head"></thead>
                <tbody id="inventory-table-body"></tbody>
              </table>
            </div>
            <div id="inventory-pagination" class="journal-pagination"></div>
          </div>
          <div id="tasks-tab-content" hidden>
            <div class="data-view-header">
              <div class="data-view-title-row">
                <div class="data-view-title-block">
                  <h2 data-i18n="tasks.title">${t("tasks.title")}</h2>
                  <p class="data-view-summary" id="tasks-summary"></p>
                </div>
                <div class="data-view-actions title-actions">
                  <button type="button" id="tasks-generate-btn" class="btn-sm" data-i18n="tasks.generate">${t("tasks.generate")}</button>
                  <button type="button" id="tasks-refresh-desc-btn" class="btn-sm" data-i18n="tasks.regenerate_reasons">${t("tasks.regenerate_reasons")}</button>
                  <button type="button" id="tasks-add-btn" class="btn-primary btn-sm" data-i18n="tasks.add">${t("tasks.add")}</button>
                </div>
              </div>
              <div id="tasks-operation-progress" class="task-operation-progress" hidden>
                <div class="task-operation-progress-copy">
                  <span id="tasks-operation-label">${t("tasks.progress_generating")}</span>
                  <span id="tasks-operation-detail">${t("tasks.progress_generating_detail")}</span>
                </div>
                <progress id="tasks-operation-bar" max="1"></progress>
              </div>
              <div class="tasks-filters">
                <div class="tasks-view-toggle" role="group" aria-label="${t("tasks.view_label")}">
                  <button type="button" class="tasks-view-btn active" data-tasks-view="today" data-i18n="tasks.view_today">${t("tasks.view_today")}</button>
                  <button type="button" class="tasks-view-btn" data-tasks-view="week" data-i18n="tasks.view_week">${t("tasks.view_week")}</button>
                  <button type="button" class="tasks-view-btn" data-tasks-view="month" data-i18n="tasks.view_month">${t("tasks.view_month")}</button>
                  <button type="button" class="tasks-view-btn" data-tasks-view="overdue" data-i18n="tasks.view_overdue">${t("tasks.view_overdue")}</button>
                </div>
                <select id="tasks-filter-type" class="select-sm" aria-label="${t("tasks.all_types")}">
                  <option value="" data-i18n="tasks.all_types">${t("tasks.all_types")}</option>
                  <option value="water">${t("tasks.type_water")}</option>
                  <option value="protect">${t("tasks.type_protect")}</option>
                  <option value="prune">${t("tasks.type_prune")}</option>
                  <option value="deadhead">${t("tasks.type_deadhead")}</option>
                  <option value="divide">${t("tasks.type_divide")}</option>
                  <option value="fertilize">${t("tasks.type_fertilize")}</option>
                  <option value="sow">${t("tasks.type_sow")}</option>
                  <option value="plant_out">${t("tasks.type_plant_out")}</option>
                  <option value="observe_bloom">${t("tasks.type_observe_bloom")}</option>
                  <option value="harvest">${t("tasks.type_harvest")}</option>
                  <option value="inspect_issue">${t("tasks.type_inspect_issue")}</option>
                </select>
                <select id="tasks-filter-status" class="select-sm" aria-label="${t("tasks.all_statuses")}">
                  <option value="" data-i18n="tasks.all_statuses">${t("tasks.all_statuses")}</option>
                  <option value="pending">${t("tasks.status_pending")}</option>
                  <option value="completed">${t("tasks.status_completed")}</option>
                  <option value="skipped">${t("tasks.status_skipped")}</option>
                  <option value="snoozed">${t("tasks.status_snoozed")}</option>
                </select>
              </div>
            </div>
            <div id="tasks-export-bar"></div>
            <div id="tasks-batch-bar" hidden></div>
            <div id="tasks-list" aria-live="polite"></div>
            <div id="tasks-pagination" class="journal-pagination"></div>
          </div>
          <div id="calendar-tab-content" hidden>
            <div class="data-view-header">
              <div class="data-view-title-row">
                <div class="data-view-title-block">
                  <h2 data-i18n="calendar.title">${t("calendar.title")}</h2>
                  <p class="data-view-summary" id="calendar-summary">${t("calendar.summary_none")}</p>
                </div>
                <div class="data-view-actions">
                  <button type="button" id="calendar-new-event-btn" class="btn btn-sm" data-i18n="calendar.new_event" hidden>${t("calendar.new_event")}</button>
                  <button type="button" id="calendar-export-btn" class="btn btn-sm" data-i18n="calendar.export">${t("calendar.export")}</button>
                  <button type="button" id="calendar-new-feed-btn" class="btn btn-sm btn-primary" data-i18n="calendar.new_feed" hidden>${t("calendar.new_feed")}</button>
                </div>
              </div>
              <div class="calendar-toolbar">
                <div class="calendar-view-toggle" role="group" aria-label="${t("calendar.view_label")}" data-i18n-aria-label="calendar.view_label">
                  <button type="button" class="calendar-view-btn active" data-calendar-view="month" data-i18n="calendar.view_month">${t("calendar.view_month")}</button>
                  <button type="button" class="calendar-view-btn" data-calendar-view="week" data-i18n="calendar.view_week">${t("calendar.view_week")}</button>
                  <button type="button" class="calendar-view-btn" data-calendar-view="agenda" data-i18n="calendar.view_agenda">${t("calendar.view_agenda")}</button>
                </div>
                <div class="calendar-nav-toolbar">
                  <button type="button" id="calendar-prev-btn" class="btn btn-sm" data-i18n="calendar.nav_prev">${t("calendar.nav_prev")}</button>
                  <button type="button" id="calendar-today-btn" class="btn btn-sm" data-i18n="calendar.nav_today">${t("calendar.nav_today")}</button>
                  <button type="button" id="calendar-next-btn" class="btn btn-sm" data-i18n="calendar.nav_next">${t("calendar.nav_next")}</button>
                  <span id="calendar-range-label" class="calendar-range-label"></span>
                </div>
                <label class="calendar-preset-field" for="calendar-preset-select">
                  <span data-i18n="calendar.preset_label">${t("calendar.preset_label")}</span>
                  <select id="calendar-preset-select"></select>
                </label>
                <label class="calendar-history-toggle" for="calendar-recent-history">
                  <input id="calendar-recent-history" type="checkbox" checked />
                  <span data-i18n="calendar.recent_history">${t("calendar.recent_history")}</span>
                </label>
              </div>
              <div class="calendar-filter-grid">
                <div id="calendar-zone-filter" class="calendar-area-filter"></div>
                <div id="calendar-plot-filter" class="calendar-area-filter"></div>
              </div>
              <div id="calendar-plant-filter" class="calendar-plant-filter"></div>
              <div id="calendar-filter-state" class="calendar-filter-state" hidden></div>
              <div id="calendar-source-filters" class="calendar-source-filters"></div>
              <div id="calendar-loading" class="calendar-loading" hidden data-i18n="calendar.loading">${t("calendar.loading")}</div>
            </div>
            <div class="calendar-layout">
              <div class="calendar-surface">
                <div id="calendar-root" class="calendar-root" aria-live="polite"></div>
              </div>
              <aside id="calendar-detail-panel" class="calendar-detail-panel">
                <div class="calendar-detail-empty" data-i18n="calendar.select_event">${t("calendar.select_event")}</div>
              </aside>
            </div>
            <section id="calendar-subscriptions-panel" class="calendar-subscriptions-panel" hidden></section>
          </div>
          <div id="issues-tab-content" hidden>
            <div class="data-view-header">
              <div class="data-view-title-row">
                <div class="data-view-title-block">
                  <h2 data-i18n="issues.title">${t("issues.title")}</h2>
                  <p class="data-view-summary" id="issues-summary"></p>
                </div>
                <div class="data-view-actions">
                  <button type="button" id="issues-add-btn" class="btn btn-sm btn-primary" data-i18n="issues.add">${t("issues.add")}</button>
                </div>
              </div>
              <div class="issues-filters">
                <select id="issues-filter-status" class="select-sm">
                  <option value="" data-i18n="issues.all_statuses">${t("issues.all_statuses")}</option>
                  <option value="open">${t("issues.status_open")}</option>
                  <option value="monitoring">${t("issues.status_monitoring")}</option>
                  <option value="treating">${t("issues.status_treating")}</option>
                  <option value="resolved">${t("issues.status_resolved")}</option>
                  <option value="dismissed">${t("issues.status_dismissed")}</option>
                </select>
                <select id="issues-filter-type" class="select-sm">
                  <option value="" data-i18n="issues.all_types">${t("issues.all_types")}</option>
                  <option value="pest">${t("issues.type_pest")}</option>
                  <option value="disease">${t("issues.type_disease")}</option>
                  <option value="fungal">${t("issues.type_fungal")}</option>
                  <option value="nutrient">${t("issues.type_nutrient")}</option>
                  <option value="environmental">${t("issues.type_environmental")}</option>
                  <option value="damage">${t("issues.type_damage")}</option>
                  <option value="other">${t("issues.type_other")}</option>
                </select>
                <select id="issues-filter-severity" class="select-sm">
                  <option value="" data-i18n="issues.all_severities">${t("issues.all_severities")}</option>
                  <option value="critical">${t("issues.severity_critical")}</option>
                  <option value="high">${t("issues.severity_high")}</option>
                  <option value="normal">${t("issues.severity_normal")}</option>
                  <option value="low">${t("issues.severity_low")}</option>
                </select>
              </div>
            </div>
            <div id="issues-export-bar"></div>
            <div id="issues-list" aria-live="polite"></div>
            <div id="issues-pagination" class="journal-pagination"></div>
          </div>
          <div id="harvest-tab-content" hidden>
            <div class="data-view-header">
              <div class="data-view-title-row">
                <div class="data-view-title-block">
                  <h2 data-i18n="harvest.title">${t("harvest.title")}</h2>
                  <p class="data-view-summary" id="harvest-summary"></p>
                </div>
                <div class="data-view-actions">
                  <button type="button" id="harvest-summary-btn" class="btn btn-sm" data-i18n="harvest.show_summary">${t("harvest.show_summary")}</button>
                  <button type="button" id="harvest-add-btn" class="btn btn-sm btn-primary" data-i18n="harvest.add">${t("harvest.add")}</button>
                </div>
              </div>
              <div class="harvest-filters">
                <select id="harvest-filter-quality" class="select-sm">
                  <option value="">${t("harvest.all_qualities")}</option>
                  <option value="excellent">${t("harvest.quality_excellent")}</option>
                  <option value="good">${t("harvest.quality_good")}</option>
                  <option value="fair">${t("harvest.quality_fair")}</option>
                  <option value="poor">${t("harvest.quality_poor")}</option>
                </select>
                <input type="date" id="harvest-filter-from" class="input-sm" />
                <input type="date" id="harvest-filter-to" class="input-sm" />
              </div>
            </div>
            <div id="harvest-export-bar"></div>
            <div id="harvest-summary-panel" hidden></div>
            <div id="harvest-list" aria-live="polite"></div>
            <div id="harvest-pagination" class="journal-pagination"></div>
          </div>
          <div id="procurement-tab-content" hidden>
            <div class="data-view-header">
              <div class="data-view-title-row">
                <div class="data-view-title-block">
                  <h2 data-i18n="procurement.title">${t("procurement.title")}</h2>
                  <p class="data-view-summary" id="procurement-summary"></p>
                </div>
                <div class="data-view-actions">
                  <button type="button" id="procurement-add-btn" class="btn btn-sm btn-primary" data-i18n="procurement.add">${t("procurement.add")}</button>
                </div>
              </div>
              <div class="procurement-filters">
                <select id="procurement-filter-status" class="select-sm">
                  <option value="">${t("procurement.all_statuses")}</option>
                  <option value="wanted">${t("procurement.status_wanted")}</option>
                  <option value="ordered">${t("procurement.status_ordered")}</option>
                  <option value="shipped">${t("procurement.status_shipped")}</option>
                  <option value="received">${t("procurement.status_received")}</option>
                  <option value="cancelled">${t("procurement.status_cancelled")}</option>
                </select>
                <select id="procurement-filter-type" class="select-sm">
                  <option value="">${t("procurement.all_types")}</option>
                  <option value="seed">${t("inventory.type.seed")}</option>
                  <option value="bulb">${t("inventory.type.bulb")}</option>
                  <option value="tuber">${t("inventory.type.tuber")}</option>
                  <option value="nursery">${t("inventory.type.nursery")}</option>
                  <option value="bare_root">${t("inventory.type.bare_root")}</option>
                  <option value="cutting">${t("inventory.type.cutting")}</option>
                  <option value="division">${t("inventory.type.division")}</option>
                  <option value="other">${t("inventory.type.other")}</option>
                </select>
              </div>
            </div>
            <div id="procurement-export-bar"></div>
            <div id="procurement-list" aria-live="polite"></div>
            <div id="procurement-pagination" class="journal-pagination"></div>
          </div>
          <div id="indoor-tab-content" hidden></div>
        </section>

        <section id="care-view" class="view" role="tabpanel" aria-labelledby="top-tab-insights" hidden>
          ${getInsightsModeToggleMarkup()}
          <div id="weather-dashboard" class="weather-dashboard"></div>
          <div class="data-view-header">
            <div class="data-view-title-row">
              <div class="data-view-title-block">
                <h2 data-i18n="care.title">${t("care.title")}</h2>
                <p id="care-summary" class="plants-summary" data-i18n="care.subtitle">${t("care.subtitle")}</p>
              </div>
              <div class="title-actions">
                <button
                  id="generate-care-btn"
                  class="btn-primary"
                  type="button"
                  disabled
                  title="${t("care.loading_title")}"
                  data-i18n-title="care.loading_title"
                >${t("care.loading")}</button>
              </div>
            </div>
            <div id="care-generation-progress" class="care-generation-progress" hidden>
              <div class="care-generation-progress-copy">
                <span id="care-generation-label">${t("care.progress_preparing")}</span>
                <span id="care-generation-count">0 / 0</span>
              </div>
              <progress id="care-generation-bar" max="1" value="0"></progress>
            </div>
            <div class="filter-row">
              <input id="care-search" type="text" placeholder="${t("care.search_placeholder")}" data-i18n-placeholder="care.search_placeholder" />
              <select id="care-category">
                <option value="" data-i18n="category.all">${t("category.all")}</option>
                <option value="løk" data-i18n="category.lok">${t("category.lok")}</option>
                <option value="frø" data-i18n="category.fro">${t("category.fro")}</option>
                <option value="busker" data-i18n="category.busker">${t("category.busker")}</option>
                <option value="baerbusker" data-i18n="category.baerbusker">${t("category.baerbusker")}</option>
                <option value="trær" data-i18n="category.traer">${t("category.traer")}</option>
              </select>
              <div class="mobile-sort-controls">
                <select id="care-sort-field" aria-label="${t("care.sort_by")}" data-i18n-aria-label="care.sort_by">
                  <option value="name" data-i18n="plants.sort_name">${t("plants.sort_name")}</option>
                  <option value="latin" data-i18n="plants.sort_latin">${t("plants.sort_latin")}</option>
                </select>
                <button id="care-sort-dir" class="sort-dir-btn" type="button" aria-label="${t("care.sort_toggle_current", { direction: "asc" })}">${t("common.asc")}</button>
              </div>
            </div>
          </div>
          <div id="care-mobile-list" class="mobile-data-list" aria-live="polite"></div>
          <div class="table-wrap desktop-data-table-wrap">
            <table class="data-table care-table">
              <thead id="care-table-head"></thead>
              <tbody id="care-table-body"></tbody>
            </table>
          </div>
        </section>

        <section id="analysis-view" class="view" role="tabpanel" aria-labelledby="top-tab-insights" hidden>
          ${getInsightsModeToggleMarkup()}
          <div class="analysis-shell">
            <div class="analysis-header">
              <div class="analysis-header-copy">
                <p class="analysis-kicker" data-i18n="analysis.kicker">${t("analysis.kicker")}</p>
                <h2 data-i18n="analysis.title">${t("analysis.title")}</h2>
                <p class="analysis-subtitle" data-i18n="analysis.subtitle">${t("analysis.subtitle")}</p>
              </div>
              <button id="clear-chat-btn" type="button" data-i18n="analysis.clear_chat">${t("analysis.clear_chat")}</button>
            </div>
            <div id="analysis-messages" class="analysis-messages">
              ${getAnalysisStartersMarkup()}
            </div>
            <div class="analysis-input-row">
              <label class="analysis-input-shell" for="analysis-input">
                <span class="analysis-input-label" data-i18n="analysis.input_label">${t("analysis.input_label")}</span>
                <input id="analysis-input" type="text" placeholder="${t("analysis.input_placeholder")}" data-i18n-placeholder="analysis.input_placeholder" autocomplete="off" />
              </label>
              <button id="analysis-send-btn" type="button" data-i18n="analysis.send">${t("analysis.send")}</button>
            </div>
          </div>
        </section>

        <section id="statistics-view" class="view" role="tabpanel" aria-labelledby="top-tab-insights" hidden>
          ${getInsightsModeToggleMarkup()}
          <div class="statistics-mode-toggle" role="tablist" aria-label="${t("stats.mode_label")}" data-i18n-aria-label="stats.mode_label">
            <button role="tab" aria-selected="true" data-stats-mode="today" id="stats-mode-today" data-i18n="stats.mode_today">${t("stats.mode_today")}</button>
            <button role="tab" aria-selected="false" data-stats-mode="overview" id="stats-mode-overview" data-i18n="stats.mode_overview">${t("stats.mode_overview")}</button>
            <button role="tab" aria-selected="false" data-stats-mode="reports" id="stats-mode-reports" data-i18n="stats.mode_reports">${t("stats.mode_reports")}</button>
            <button role="tab" aria-selected="false" data-stats-mode="planner" id="stats-mode-planner" data-i18n="stats.mode_planner">${t("stats.mode_planner")}</button>
          </div>
          <div id="statistics-export-bar" class="statistics-export-bar"></div>
          <div class="statistics-scroll-region">
            <div id="today-dashboard" class="today-dashboard"></div>
            <div id="statistics-content" class="statistics-content" hidden></div>
            <div id="reports-dashboard" class="reports-dashboard" hidden></div>
            <div id="planner-dashboard" class="planner-dashboard" hidden></div>
          </div>
        </section>

        ${getAdminViewMarkup()}

      </main>

      <div id="mobile-utility-backdrop" class="mobile-utility-backdrop" aria-hidden="true"></div>
      <aside id="mobile-utility-sheet" class="mobile-utility-sheet" aria-hidden="true" aria-labelledby="mobile-utility-title">
        <div class="mobile-utility-sheet-header">
          <div>
            <p class="mobile-utility-kicker" data-i18n="nav.garden_controls">${t("nav.garden_controls")}</p>
            <h2 id="mobile-utility-title" data-i18n="nav.quick_actions">${t("nav.quick_actions")}</h2>
          </div>
          <button id="mobile-utility-close-btn" class="close-btn" type="button" aria-label="${t("nav.close_controls")}" data-i18n-aria-label="nav.close_controls">&times;</button>
        </div>

        <div class="mobile-utility-section global-search-shell">
          <label class="mobile-field" for="mobile-global-plant-search">
            <span data-i18n="nav.find_plant_on_map">${t("nav.find_plant_on_map")}</span>
            <input
              id="mobile-global-plant-search"
              class="global-search-input"
              data-dropdown-id="mobile-global-search-dropdown"
              type="search"
              name="plant-search-mobile"
              placeholder="${t("nav.highlight_plant")}"
              data-i18n-placeholder="nav.highlight_plant"
              autocomplete="off"
            />
          </label>
          <div id="mobile-global-search-dropdown" class="search-dropdown global-search-dropdown mobile-search-dropdown" aria-live="polite" hidden></div>
        </div>

        <div class="mobile-utility-section">
          <label class="mobile-field" for="mobile-garden-select">
            <span data-i18n="nav.active_garden">${t("nav.active_garden")}</span>
            <select id="mobile-garden-select" class="garden-select mobile-garden-select" data-garden-select aria-label="${t("nav.active_garden")}" hidden></select>
          </label>
          <div class="mobile-inline-row">
            <button id="mobile-garden-create-btn" class="mobile-chip-button" data-garden-create type="button" data-i18n="nav.create_garden_mobile" hidden>${t("nav.create_garden_mobile")}</button>
            <span id="mobile-garden-role-chip" class="garden-role-chip mobile-role-chip" data-garden-role hidden></span>
          </div>
        </div>

        <div class="mobile-utility-section">
          <label class="mobile-field">
            <span data-i18n="common.language">${t("common.language")}</span>
            ${getLocaleSwitchMarkup("mobile")}
          </label>
        </div>

        <div class="mobile-utility-section mobile-action-grid">
          <button id="mobile-admin-btn" class="mobile-action-btn" type="button">${t("nav.settings_user")}</button>
          <button id="mobile-notification-btn" class="mobile-action-btn" type="button">
            ${t("notifications.title")} <span id="mobile-notification-badge" class="notification-badge-inline" hidden>0</span>
          </button>
          <button id="mobile-auth-btn" class="mobile-action-btn" data-auth-trigger type="button">${t("nav.sign_in")}</button>
          <button id="mobile-theme-toggle" class="mobile-action-btn" data-theme-toggle type="button" aria-label="${t("nav.toggle_theme")}">${t("common.theme")}</button>
        </div>

        <p class="mobile-utility-meta app-clock" id="mobile-top-clock"></p>
      </aside>

      <div id="mobile-map-sheet-backdrop" class="mobile-map-sheet-backdrop" aria-hidden="true"></div>

      <aside id="mobile-map-layouts-sheet" class="mobile-map-sheet" aria-hidden="true" aria-labelledby="mobile-map-layouts-title">
        <div class="mobile-map-sheet-header">
          <div>
            <p class="mobile-map-sheet-kicker" data-i18n="map.saved_layouts">${t("map.saved_layouts")}</p>
            <h2 id="mobile-map-layouts-title" data-i18n="map.garden_layouts">${t("map.garden_layouts")}</h2>
          </div>
          <button id="mobile-map-layouts-close-btn" class="close-btn" type="button" aria-label="${t("map.close_layouts")}" data-i18n-aria-label="map.close_layouts">&times;</button>
        </div>
        <div class="mobile-map-sheet-actions">
          <button id="mobile-map-layouts-save-btn" class="mobile-map-sheet-btn mobile-map-sheet-btn--primary" type="button" data-i18n="map.save_current_layout">${t("map.save_current_layout")}</button>
        </div>
        <div id="mobile-snapshots-list" class="mobile-snapshots-list" aria-live="polite"></div>
      </aside>

      <aside id="mobile-map-tools-sheet" class="mobile-map-sheet" aria-hidden="true" aria-labelledby="mobile-map-tools-title">
        <div class="mobile-map-sheet-header">
          <div>
            <p class="mobile-map-sheet-kicker" data-i18n="map.map_controls">${t("map.map_controls")}</p>
            <h2 id="mobile-map-tools-title" data-i18n="map.map_tools">${t("map.map_tools")}</h2>
          </div>
          <button id="mobile-map-tools-close-btn" class="close-btn" type="button" aria-label="${t("map.close_map_tools")}" data-i18n-aria-label="map.close_map_tools">&times;</button>
        </div>

        <div class="mobile-map-tool-card">
          <p class="mobile-map-tool-title" data-i18n="map.north_calibration">${t("map.north_calibration")}</p>
          <div class="mobile-map-stepper">
            <button id="mobile-map-direction-dec-btn" class="mobile-map-sheet-btn" type="button">-5°</button>
            <label class="mobile-map-number-field" for="mobile-map-direction-input">
              <span data-i18n="map.north_calibration">${t("map.north_calibration")}</span>
              <div class="mobile-map-number-wrap">
                <input id="mobile-map-direction-input" type="number" min="0" max="359" step="1" value="0" inputmode="numeric" />
                <span>°</span>
              </div>
            </label>
            <button id="mobile-map-direction-inc-btn" class="mobile-map-sheet-btn" type="button">+5°</button>
          </div>
        </div>

        <div class="mobile-map-tool-card">
          <p class="mobile-map-tool-title" data-i18n="map.property_size">${t("map.property_size")}</p>
          <div class="mobile-map-grid-fields">
            <label for="mobile-grid-cols-input">
              <span data-i18n="onboarding.width">${t("onboarding.width")}</span>
              <input id="mobile-grid-cols-input" type="number" min="5" max="100" step="1" value="22" inputmode="numeric" />
            </label>
            <label for="mobile-grid-rows-input">
              <span data-i18n="onboarding.depth">${t("onboarding.depth")}</span>
              <input id="mobile-grid-rows-input" type="number" min="5" max="100" step="1" value="30" inputmode="numeric" />
            </label>
          </div>
          <button id="mobile-grid-dims-apply-btn" class="mobile-map-sheet-btn mobile-map-sheet-btn--primary" type="button" data-i18n="map.apply_property_size">${t("map.apply_property_size")}</button>
        </div>

        <div class="mobile-map-tool-card">
          <p class="mobile-map-tool-title" data-i18n="map.zones">${t("map.zones")}</p>
          <button id="mobile-create-zone-btn" class="mobile-map-sheet-btn" type="button" data-i18n="map.create_zone">${t("map.create_zone")}</button>
        </div>

        <div class="mobile-map-tool-card">
          <p class="mobile-map-tool-title" data-i18n="map.files">${t("map.files")}</p>
          <div class="mobile-map-sheet-actions">
            <button id="mobile-export-map-btn" class="mobile-map-sheet-btn" type="button" data-i18n="map.export_map">${t("map.export_map")}</button>
            <button id="mobile-import-map-btn" class="mobile-map-sheet-btn" type="button" data-i18n="map.import_map">${t("map.import_map")}</button>
          </div>
        </div>

        <p class="mobile-map-note" data-i18n="map.mobile_note">${t("map.mobile_note")}</p>
      </aside>

      <button id="mobile-fab" class="mobile-fab" type="button" aria-label="${t("quick_actions.title")}" data-i18n-aria-label="quick_actions.title">+</button>
      <div id="mobile-fab-backdrop" class="mobile-fab-backdrop" aria-hidden="true"></div>
      <aside id="mobile-quick-actions" class="mobile-quick-actions" aria-hidden="true">
        <div class="mobile-quick-actions-handle"></div>
        <div id="mobile-quick-actions-content"></div>
      </aside>

      <nav class="mobile-tabbar" aria-label="${t("nav.main_sections")}" data-i18n-aria-label="nav.main_sections">
        <button id="mobile-tab-map" class="mobile-tab-btn active" data-tab="map" type="button" aria-current="page" data-i18n="nav.map">${t("nav.map")}</button>
        <button id="mobile-tab-garden" class="mobile-tab-btn" data-tab="garden" type="button" data-i18n="nav.garden">${t("nav.garden")}</button>
        <button id="mobile-tab-activity" class="mobile-tab-btn" data-tab="activity" type="button" data-i18n="nav.activity">${t("nav.activity")}</button>
        <button id="mobile-tab-insights" class="mobile-tab-btn" data-tab="insights" type="button" data-i18n="nav.insights">${t("nav.insights")}</button>
      </nav>
    </div>
  `;
}
