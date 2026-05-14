import type { AppContext } from "../core/appContext";
import type { WeatherSummary } from "../core/models";
import { t } from "../core/i18n";
import { renderWeatherDashboard } from "../components/weather";
import {
  fetchWeatherSummaryApi,
  checkWeatherApi,
  dismissWeatherAlertApi,
  getApiErrorMessage,
} from "../services/api";

let ctx: AppContext;
let weatherSummary: WeatherSummary | null = null;

export function initWeatherFeature(
  appCtx: AppContext,
): void {
  ctx = appCtx;
}

export function getWeatherSummary(): WeatherSummary | null {
  return weatherSummary;
}

export async function loadWeather(): Promise<void> {
  try {
    weatherSummary = await fetchWeatherSummaryApi();
    const container = document.getElementById(
      "weather-dashboard",
    );
    if (container) {
      renderWeatherDashboard(
        container,
        weatherSummary,
        {
          onDismissAlert: async (alert) => {
            try {
              await dismissWeatherAlertApi(alert.id);
              void loadWeather();
            } catch (err) {
              ctx.showToast(
                getApiErrorMessage(err),
                "error",
              );
            }
          },
          onPlantClick: (pltId) => {
            ctx.focusPlantsInPlantsView([pltId]);
          },
          onCheckWeather: async () => {
            try {
              const result =
                await checkWeatherApi();
              ctx.showToast(
                t("weather.check_result", {
                  created: String(
                    result.alerts_created,
                  ),
                  available: String(
                    result.forecast_available,
                  ),
                }),
                "success",
              );
              void loadWeather();
            } catch (err) {
              ctx.showToast(
                getApiErrorMessage(err),
                "error",
              );
            }
          },
        },
      );
    }
  } catch {
    // Weather is non-critical -- don't show errors
  }
}
