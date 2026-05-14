import type { Plant } from "../core/models";
import { t } from "../core/i18n";
import { sanitizeUrl } from "../core/sanitize";
import type { MediaAsset } from "../services/api";
import { openMediaLightboxLazy } from "./mediaGalleryLoader";
import { formatBloomMonth } from "./dataTables";

export type PlantAlertType = "task" | "issue" | "weather";

const ALERT_LABELS: Record<PlantAlertType, string> = {
  task: "plot_drawer.alert_task",
  issue: "plot_drawer.alert_issue",
  weather: "plot_drawer.alert_weather",
};

export function renderPlantCard(
  plant: Plant,
  plotId: string,
  options: {
    mediaPreview?: MediaAsset | null | undefined;
    alertTypes?: PlantAlertType[] | undefined;
    onCreateCalendarEvent?: ((plant: Plant) => void) | undefined;
    canWrite?: boolean | undefined;
  } = {},
): HTMLElement {
  const card = document.createElement("div");
  card.className = "plant-card";
  card.draggable = options.canWrite !== false;
  card.dataset["pltId"] = plant.plt_id;
  card.dataset["fromPlot"] = plotId;

  const header = document.createElement("div");
  header.className = "plant-header";

  const name = document.createElement("strong");
  name.textContent = plant.name;

  const actions = document.createElement("div");
  actions.className = "plant-actions";

  const safeLink = sanitizeUrl(plant.link ?? "");
  if (safeLink) {
    const link = document.createElement("a");
    link.href = safeLink;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "\uD83D\uDD17";
    actions.appendChild(link);
  }

  if (options.canWrite !== false) {
    const editButton = document.createElement("button");
    editButton.className = "edit-plant-btn";
    editButton.dataset["edit"] = plant.plt_id;
    editButton.setAttribute("aria-label", `${t("plants.edit_plant")}: ${plant.name}`);
    editButton.title = t("plants.edit");
    editButton.type = "button";
    editButton.textContent = "\u270E";
    actions.appendChild(editButton);
  }

  if (options.canWrite !== false && options.onCreateCalendarEvent) {
    const calendarButton = document.createElement("button");
    calendarButton.className = "plant-calendar-btn";
    calendarButton.dataset["calendarCreatePlant"] = plant.plt_id;
    calendarButton.setAttribute(
      "aria-label",
      t("plants.card_calendar_aria", { name: plant.name }),
    );
    calendarButton.title = t("calendar.new_event");
    calendarButton.type = "button";
    calendarButton.textContent = "Cal";
    actions.appendChild(calendarButton);
  }

  if (options.canWrite !== false) {
    const removeButton = document.createElement("button");
    removeButton.className = "remove-btn";
    removeButton.dataset["remove"] = plant.plt_id;
    removeButton.setAttribute("aria-label", t("plants.card_remove_aria", { name: plant.name, plot: plotId }));
    removeButton.type = "button";
    removeButton.textContent = "\u00d7";
    actions.appendChild(removeButton);
  }

  header.append(name, actions);

  if (options.mediaPreview) {
    const heroWrap = document.createElement("div");
    heroWrap.className = "plant-card-hero";
    heroWrap.setAttribute("role", "img");
    heroWrap.setAttribute("aria-label", `${plant.name} · ${t("media.latest_photo")}`);
    const heroImg = document.createElement("img");
    heroImg.className = "plant-card-hero-image";
    heroImg.src = options.mediaPreview.preview_url;
    heroImg.alt = "";
    heroImg.loading = "lazy";
    heroWrap.appendChild(heroImg);
    heroWrap.addEventListener("click", () => {
      openMediaLightboxLazy(
        options.mediaPreview!.original_url,
        options.mediaPreview!.original_filename || t("media.lightbox_title"),
      );
    });
    card.appendChild(heroWrap);
  }

  card.appendChild(header);

  if (options.alertTypes && options.alertTypes.length > 0) {
    const alertRow = document.createElement("div");
    alertRow.className = "plant-card-alerts";
    for (const alertType of options.alertTypes) {
      const tag = document.createElement("span");
      tag.className =
        `plant-alert-tag plant-alert-tag--${alertType}`;
      tag.textContent = t(ALERT_LABELS[alertType]);
      alertRow.appendChild(tag);
    }
    card.appendChild(alertRow);
  }

  if (plant.latin) {
    const latin = document.createElement("div");
    latin.className = "plant-latin";
    const em = document.createElement("em");
    em.textContent = plant.latin;
    latin.appendChild(em);
    card.appendChild(latin);
  }

  const details = [
    plant.height_cm ? `${plant.height_cm} cm` : "",
    plant.bloom_month ? `${t("plants.field_bloom")}: ${formatBloomMonth(plant.bloom_month)}` : "",
    plant.color ? `${t("plants.field_color")}: ${plant.color}` : "",
    plant.light ? `${t("plants.field_light")}: ${plant.light}` : "",
    plant.quantity ? `${t("plants.qty_chip", { count: plant.quantity })}` : "",
  ].filter(Boolean).join(" \u2022 ");

  const detail = document.createElement("div");
  detail.className = "plant-details";
  detail.textContent = details;
  card.appendChild(detail);

  return card;
}
