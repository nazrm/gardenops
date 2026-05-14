import { t } from "../core/i18n";
import { getExportUrl } from "../services/api";

export type ExportBarResource =
  | "plants"
  | "inventory"
  | "tasks"
  | "journal"
  | "harvest"
  | "issues"
  | "procurement";

export interface ExportBarCallbacks {
  onPrint: () => void;
}

export function renderExportBar(
  container: HTMLElement,
  resource: ExportBarResource,
  callbacks: ExportBarCallbacks,
  params?: Record<string, string>,
): void {
  container.replaceChildren();
  const bar = document.createElement("div");
  bar.className = "export-bar";

  const csvBtn = document.createElement("a");
  csvBtn.className = "btn btn-sm btn-secondary";
  csvBtn.href = getExportUrl(resource, "csv", params);
  csvBtn.download = "";
  csvBtn.textContent = t("exports.download_csv");

  const jsonBtn = document.createElement("a");
  jsonBtn.className = "btn btn-sm btn-secondary";
  jsonBtn.href = getExportUrl(resource, "json", params);
  jsonBtn.download = "";
  jsonBtn.textContent = t("exports.download_json");

  const printBtn = document.createElement("button");
  printBtn.type = "button";
  printBtn.className = "btn btn-sm btn-secondary";
  printBtn.textContent = t("exports.print");
  printBtn.addEventListener("click", callbacks.onPrint);

  bar.append(csvBtn, jsonBtn, printBtn);
  container.appendChild(bar);
}
