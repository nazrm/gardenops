import type { JournalEntry, JournalEventType } from "../core/models";
import { getLocaleTag, t } from "../core/i18n";

const JOURNAL_EVENT_TRANSLATION_KEYS: Record<JournalEventType, string> = {
  planted: "journal.event.planted",
  moved: "journal.event.moved",
  divided: "journal.event.divided",
  pruned: "journal.event.pruned",
  watered: "journal.event.watered",
  fertilized: "journal.event.fertilized",
  bloomed: "journal.event.bloomed",
  harvested: "journal.event.harvested",
  died: "journal.event.died",
  observed: "journal.event.observed",
};

export const JOURNAL_EVENT_ICONS: Record<JournalEventType, string> = {
  planted: "\u{1F331}",
  moved: "\u{1F4E6}",
  divided: "\u{2702}\uFE0F",
  pruned: "\u{2702}\uFE0F",
  watered: "\u{1F4A7}",
  fertilized: "\u{1F33E}",
  bloomed: "\u{1F338}",
  harvested: "\u{1F34E}",
  died: "\u{1F342}",
  observed: "\u{1F50D}",
};

export function formatJournalDate(isoDate: string): string {
  try {
    const date = new Date(isoDate + "T00:00:00");
    return date.toLocaleDateString(getLocaleTag(), {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return isoDate;
  }
}

export function journalEventLabel(
  eventType: JournalEventType,
): string {
  return t(
    JOURNAL_EVENT_TRANSLATION_KEYS[eventType] ?? eventType,
  );
}

export function renderPlotJournalPreview(
  container: HTMLElement,
  entries: JournalEntry[],
  onViewAll: () => void,
): void {
  container.replaceChildren();
  if (entries.length === 0) return;

  const heading = document.createElement("div");
  heading.className = "journal-preview-heading";
  heading.textContent = t("journal.preview_recent");
  container.appendChild(heading);

  const shown = entries.slice(0, 3);
  for (const entry of shown) {
    const row = document.createElement("div");
    row.className = "journal-preview-row";

    const icon = document.createElement("span");
    icon.className = "journal-preview-icon";
    icon.textContent = JOURNAL_EVENT_ICONS[entry.event_type] ?? "";

    const text = document.createElement("span");
    text.className = "journal-preview-text";
    const label = journalEventLabel(entry.event_type);
    text.textContent = entry.title
      ? `${label}: ${entry.title}`
      : label;

    const date = document.createElement("span");
    date.className = "journal-preview-date";
    date.textContent = formatJournalDate(entry.occurred_on);

    row.append(icon, text, date);
    container.appendChild(row);
  }

  if (entries.length > 3) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "journal-preview-more";
    more.textContent = t("journal.preview_view_all", {
      count: entries.length,
    });
    more.addEventListener("click", onViewAll);
    container.appendChild(more);
  }
}
