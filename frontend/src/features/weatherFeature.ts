import type { AppContext } from "../core/appContext";
import type { WeatherSummary } from "../core/models";
import { t } from "../core/i18n";
import { renderWeatherDashboard } from "../components/weather";
import {
  fetchWeatherSummaryApi,
  checkWeatherApi,
  dismissWeatherAlertApi,
  getActiveGardenContext,
  getApiErrorMessage,
} from "../services/api";

let ctx: AppContext;
let weatherSummary: WeatherSummary | null = null;
let weatherGardenId: number | null = null;
let weatherRequestVersion = 0;

interface WeatherRequestContext {
  gardenId: number;
  version: number;
}

function weatherRequestContext(): WeatherRequestContext | null {
  const gardenId = getActiveGardenContext();
  if (gardenId === null) return null;
  if (weatherGardenId !== gardenId) {
    resetWeatherForCurrentGarden();
  }
  return { gardenId, version: weatherRequestVersion };
}

function isCurrentWeatherRequest(request: WeatherRequestContext): boolean {
  return (
    request.version === weatherRequestVersion
    && request.gardenId === weatherGardenId
    && request.gardenId === getActiveGardenContext()
  );
}

export function resetWeatherForCurrentGarden(): void {
  weatherGardenId = getActiveGardenContext();
  weatherRequestVersion += 1;
  weatherSummary = null;
  document.getElementById("weather-dashboard")?.replaceChildren();
}

export function initWeatherFeature(
  appCtx: AppContext,
): void {
  ctx = appCtx;
}

export function getWeatherSummary(): WeatherSummary | null {
  return weatherSummary;
}

export async function loadWeather(): Promise<void> {
  const request = weatherRequestContext();
  if (!request) return;
  try {
    const summary = await fetchWeatherSummaryApi();
    if (!isCurrentWeatherRequest(request)) return;
    weatherSummary = summary;
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
              if (!isCurrentWeatherRequest(request)) return;
              void loadWeather();
            } catch (err) {
              if (!isCurrentWeatherRequest(request)) return;
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
              if (!isCurrentWeatherRequest(request)) return;
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
              if (!isCurrentWeatherRequest(request)) return;
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
