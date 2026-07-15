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
  onError?: (message: string) => void;
}

function filenameFromResponse(response: Response, fallback: string): string {
  const disposition = response.headers.get("content-disposition") ?? "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded);
    } catch {
      return fallback;
    }
  }
  return disposition.match(/filename="?([^";]+)"?/i)?.[1] ?? fallback;
}

async function downloadExport(
  url: string,
  fallbackFilename: string,
): Promise<void> {
  const response = await fetch(url, { credentials: "include" });
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filenameFromResponse(response, fallbackFilename);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 100);
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
  const status = document.createElement("span");
  status.className = "export-bar-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");

  const csvBtn = document.createElement("button");
  csvBtn.type = "button";
  csvBtn.className = "btn btn-sm btn-secondary";
  csvBtn.textContent = t("exports.download_csv");

  const jsonBtn = document.createElement("button");
  jsonBtn.type = "button";
  jsonBtn.className = "btn btn-sm btn-secondary";
  jsonBtn.textContent = t("exports.download_json");

  const printBtn = document.createElement("button");
  printBtn.type = "button";
  printBtn.className = "btn btn-sm btn-secondary";
  printBtn.textContent = t("exports.print");
  printBtn.addEventListener("click", callbacks.onPrint);

  const buttons = [csvBtn, jsonBtn, printBtn];
  const runDownload = (button: HTMLButtonElement, format: "csv" | "json") => {
    const originalLabel = button.textContent ?? "";
    buttons.forEach((item) => { item.disabled = true; });
    bar.setAttribute("aria-busy", "true");
    button.textContent = t("common.loading");
    status.textContent = "";
    void downloadExport(
      getExportUrl(resource, format, params),
      `gardenops-${resource}.${format}`,
    ).catch((err: unknown) => {
      const message = err instanceof Error ? err.message : String(err);
      status.textContent = message;
      callbacks.onError?.(message);
    }).finally(() => {
      button.textContent = originalLabel;
      buttons.forEach((item) => { item.disabled = false; });
      bar.removeAttribute("aria-busy");
    });
  };
  csvBtn.addEventListener("click", () => runDownload(csvBtn, "csv"));
  jsonBtn.addEventListener("click", () => runDownload(jsonBtn, "json"));

  bar.append(csvBtn, jsonBtn, printBtn, status);
  container.appendChild(bar);
}
