import type { PlotAssignmentMeaning } from "../services/api";

export function normalizePlotAssignmentId(raw: string): string {
  return raw.trim().toUpperCase();
}

export function resolvePlotAssignmentMeaning(
  plotId: string,
  meanings: PlotAssignmentMeaning[],
): PlotAssignmentMeaning | null {
  const normalizedId = normalizePlotAssignmentId(plotId);
  if (!normalizedId) return null;

  let bestPrefix: PlotAssignmentMeaning | null = null;
  for (const meaning of meanings) {
    const pattern = normalizePlotAssignmentId(meaning.pattern);
    if (!pattern) continue;
    if (!pattern.endsWith("*")) {
      if (pattern === normalizedId) return meaning;
      continue;
    }
    const prefix = pattern.slice(0, -1);
    if (!prefix || !normalizedId.startsWith(prefix)) continue;
    if (!bestPrefix || prefix.length > bestPrefix.pattern.replace(/\*$/, "").length) {
      bestPrefix = meaning;
    }
  }
  return bestPrefix;
}

export function formatPlotAssignmentMeaning(meaning: PlotAssignmentMeaning | null): string {
  if (!meaning) return "";
  const label = meaning.label.trim();
  const description = meaning.description.trim();
  if (!label) return description;
  if (!description) return label;
  return `${label} — ${description}`;
}
