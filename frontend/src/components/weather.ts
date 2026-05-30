/**
 * Weather dashboard — pure render functions for the Care tab.
 * Shows forecast strip, weather alerts, and plant vulnerability lists.
 */

import { localizeEnum, t } from "../core/i18n";
import type { WeatherAlert, WeatherSummary } from "../core/models";
import {
  clearChildren,
  escapeHtml,
  setReviewedDynamicHtml,
  setStaticTemplateHtml,
} from "../core/sanitize";

export interface WeatherCallbacks {
  onDismissAlert: (alert: WeatherAlert) => void;
  onPlantClick: (pltId: string) => void;
  onCheckWeather: () => void;
}

const ALERT_ICONS: Record<string, string> = {
  frost_warning: "\u2744\ufe0f",
  heat_wave: "\ud83d\udd25",
  dry_spell: "\ud83c\udfdc\ufe0f",
  rain_surplus: "\ud83c\udf27\ufe0f",
};

const ADVICE_KEYS: Record<string, { perPlant: string; generic: string }> = {
  frost_warning: {
    perPlant: "weather.advice.frost_sensitive",
    generic: "weather.advice.frost_generic",
  },
  heat_wave: {
    perPlant: "weather.advice.provide_shade",
    generic: "weather.advice.shade_generic",
  },
  dry_spell: {
    perPlant: "weather.advice.needs_water",
    generic: "weather.advice.water_generic",
  },
  rain_surplus: {
    perPlant: "weather.advice.check_drainage",
    generic: "weather.advice.drainage_generic",
  },
};

function alertTemplateParams(
  alert: WeatherAlert,
): Record<string, string | number> {
  const m = alert.metadata;
  switch (alert.alert_type) {
    case "frost_warning": {
      const frostDays = Array.isArray(m["frost_days"])
        ? (m["frost_days"] as unknown[][])
        : [];
      return {
        coldest: Number(m["coldest"] ?? 0),
        coldest_date: String(m["coldest_date"] ?? ""),
        frost_day_count: frostDays.length,
        from: alert.valid_from,
        to: alert.valid_until,
      };
    }
    case "heat_wave":
      return {
        peak: Number(m["peak"] ?? 0),
        days: Number(m["days"] ?? 0),
        from: alert.valid_from,
        to: alert.valid_until,
      };
    case "dry_spell":
      return {
        days: Number(m["days"] ?? 0),
        from: alert.valid_from,
        to: alert.valid_until,
      };
    case "rain_surplus":
      return {
        rain_days: Number(m["rain_days"] ?? 0),
        total_mm: Number(m["total_mm"] ?? 0),
        from: alert.valid_from,
        to: alert.valid_until,
      };
    default:
      return {};
  }
}

function formatShortDate(dateStr: string): string {
  try {
    const d = new Date(dateStr + "T00:00:00");
    return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric" });
  } catch {
    return dateStr;
  }
}

export function renderWeatherDashboard(
  container: HTMLElement,
  summary: WeatherSummary | null,
  callbacks: WeatherCallbacks,
): void {
  if (!container) return;

  if (!summary) {
    clearChildren(container);
    return;
  }

  if (!summary.forecast_available && summary.alerts.length === 0) {
    setStaticTemplateHtml(container, `
      <div class="weather-section">
        <div class="weather-section-title">
          ${t("weather.title")}
          <div class="weather-actions">
            <button type="button" class="btn-secondary weather-check-btn">${t("weather.check")}</button>
          </div>
        </div>
        <div class="weather-no-data">${t("weather.no_forecast")}</div>
      </div>
    `);
    container.querySelector(".weather-check-btn")?.addEventListener("click", () => {
      callbacks.onCheckWeather();
    });
    return;
  }

  const sections: string[] = [];

  // Forecast strip
  if (summary.forecast_days.length > 0) {
    const days = summary.forecast_days.map((day) => {
      const isFrost = day.temp_min !== null && day.temp_min <= 0;
      const tempStr = day.temp_min !== null && day.temp_max !== null
        ? t("weather.temp_range", { min: String(Math.round(day.temp_min)), max: String(Math.round(day.temp_max)) })
        : "--";
      const precipStr = day.precipitation !== null && day.precipitation > 0
        ? t("weather.precip", { mm: String(day.precipitation.toFixed(1)) })
        : "";
      return `
        <div class="forecast-day${isFrost ? " frost" : ""}">
          <div class="forecast-day-date">${formatShortDate(day.date)}</div>
          <div class="forecast-day-temp">${tempStr}</div>
          ${precipStr ? `<div class="forecast-day-precip">${precipStr}</div>` : ""}
        </div>
      `;
    }).join("");

    sections.push(`
      <div class="weather-section">
        <div class="weather-section-title">
          ${t("weather.forecast_title")}
          <div class="weather-actions">
            <button type="button" class="btn-secondary weather-check-btn">${t("weather.refresh")}</button>
          </div>
        </div>
        <div class="forecast-strip">${days}</div>
      </div>
    `);
  }

  // Alert cards
  if (summary.alerts.length > 0) {
    const alertCards = summary.alerts.map((alert) =>
      createWeatherAlertCardMarkup(alert),
    ).join("");
    sections.push(`
      <div class="weather-section">
        <div class="weather-section-title">${t("weather.alerts_title")}</div>
        <div class="weather-alerts-list">${alertCards}</div>
      </div>
    `);
  }

  // Frost-vulnerable plants
  if (summary.frost_vulnerable_plants.length > 0) {
    const chips = summary.frost_vulnerable_plants.map((p) =>
      `<button type="button" class="weather-plant-chip frost-risk" data-plt-id="${escapeHtml(p.plt_id)}" title="${escapeHtml(p.hardiness)}">${escapeHtml(p.name)}</button>`,
    ).join("");
    sections.push(`
      <div class="weather-section">
        <div class="weather-section-title">${t("weather.frost_plants_title")}</div>
        <div class="weather-plants-list">${chips}</div>
      </div>
    `);
  }

  // Watering-sensitive plants
  if (summary.watering_sensitive_plants.length > 0) {
    const chips = summary.watering_sensitive_plants.map((p) =>
      `<button type="button" class="weather-plant-chip water-need" data-plt-id="${escapeHtml(p.plt_id)}">${escapeHtml(p.name)}</button>`,
    ).join("");
    sections.push(`
      <div class="weather-section">
        <div class="weather-section-title">${t("weather.watering_plants_title")}</div>
        <div class="weather-plants-list">${chips}</div>
      </div>
    `);
  }

  setReviewedDynamicHtml(container, sections.join(""));

  // Wire event handlers
  container.querySelector(".weather-check-btn")?.addEventListener("click", () => {
    callbacks.onCheckWeather();
  });

  container.querySelectorAll<HTMLButtonElement>(".weather-alert-dismiss").forEach((btn) => {
    btn.addEventListener("click", () => {
      const alertId = Number(btn.dataset["alertId"]);
      const alert = summary.alerts.find((a) => a.id === alertId);
      if (alert) callbacks.onDismissAlert(alert);
    });
  });

  container.querySelectorAll<HTMLButtonElement>(".weather-plant-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const pltId = chip.dataset["pltId"];
      if (pltId) callbacks.onPlantClick(pltId);
    });
  });
}

function createWeatherAlertCardMarkup(
  alert: WeatherAlert,
): string {
  const icon = ALERT_ICONS[alert.alert_type] ?? "\u26a0\ufe0f";
  const params = alertTemplateParams(alert);
  const titleText = escapeHtml(
    t(`weather.${alert.alert_type}_title`, params) || alert.title,
  );
  const descText = escapeHtml(
    t(`weather.${alert.alert_type}_desc`, params) || alert.description,
  );
  const dateRange = t("weather.valid_range", {
    from: formatShortDate(alert.valid_from),
    to: formatShortDate(alert.valid_until),
  });
  const severityLabel = localizeEnum("severity", alert.severity);

  // Per-plant advisory
  const adviceKeys = ADVICE_KEYS[alert.alert_type];
  let advisoryHtml = "";
  const plantAdvice = Array.isArray(alert.metadata["plant_advice"])
    ? (alert.metadata["plant_advice"] as Record<string, unknown>[])
    : [];

  if (plantAdvice.length > 0 && adviceKeys) {
    const lines = plantAdvice.map((p) => {
      const name = escapeHtml(String(p["name"] ?? ""));
      const advice = escapeHtml(
        t(
          adviceKeys.perPlant,
          p as Record<string, string | number>,
        ),
      );
      return `<div class="weather-advice-plant">`
        + `<strong>${name}</strong> \u2014 ${advice}</div>`;
    }).join("");
    advisoryHtml =
      `<div class="weather-advice">${lines}</div>`;
  } else if (adviceKeys) {
    const generic = escapeHtml(t(adviceKeys.generic));
    advisoryHtml =
      `<div class="weather-advice generic">${generic}</div>`;
  }

  return `
    <div class="weather-alert-card severity-${alert.severity}">
      <div class="weather-alert-header">
        <span class="weather-alert-icon">${icon}</span>
        <span class="weather-alert-title">${titleText}</span>
        <span class="weather-alert-severity severity-${alert.severity}">${severityLabel}</span>
      </div>
      <div class="weather-alert-description">${descText}</div>
      <div class="weather-alert-dates">${dateRange}</div>
      ${advisoryHtml}
      <div class="weather-alert-footer">
        <button type="button" class="weather-alert-dismiss" data-alert-id="${alert.id}">${t("weather.dismiss")}</button>
      </div>
    </div>
  `;
}
