import type { JournalEventType } from "../core/models";

export interface JournalDraftFields {
  event_type: JournalEventType;
  occurred_on: string;
  title: string;
  notes: string;
  plant_ids: string[];
  plot_ids: string[];
  photo_count: number;
}

export interface JournalDraft extends JournalDraftFields {
  id: string;
}

const PREFIX = "gardenops:journal-draft:v1:";
let generation = 0;
export function getJournalDraftGeneration(): number { return generation; }

export function journalDraftKey(identity: string, gardenId: number | null): string {
  return PREFIX + JSON.stringify([identity, gardenId]);
}

export function readJournalDraft(key: string): JournalDraft | null {
  const raw = localStorage.getItem(key);
  if (!raw) return null;
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== "object") throw new Error("Invalid journal draft");
  const d = value as Record<string, unknown>;
  if (!["id", "occurred_on", "title", "notes", "event_type"].every((k) => typeof d[k] === "string")
    || !["planted", "moved", "divided", "pruned", "watered", "fertilized", "bloomed", "harvested", "died", "observed"].includes(String(d["event_type"]))
    || !["plant_ids", "plot_ids"].every((k) => Array.isArray(d[k]) && (d[k] as unknown[]).every((id) => typeof id === "string"))
    || !Number.isSafeInteger(d["photo_count"]) || Number(d["photo_count"]) < 0) {
    throw new Error("Invalid journal draft");
  }
  return value as JournalDraft;
}

export function writeJournalDraft(key: string, draft: JournalDraft): void {
  const current = readJournalDraft(key);
  if (current && current.id !== draft.id) throw new Error("Another journal draft exists");
  localStorage.setItem(key, JSON.stringify(draft));
}

export function discardJournalDraft(key: string, id?: string): void {
  if (id && readJournalDraft(key)?.id !== id) return;
  localStorage.removeItem(key);
}

export function validateJournalDraft(
  draft: JournalDraft, plantIds: ReadonlySet<string>, plotIds: ReadonlySet<string>,
): { draft: JournalDraft; missingIds: string[] } {
  const missingIds = [
    ...draft.plant_ids.filter((id) => !plantIds.has(id)),
    ...draft.plot_ids.filter((id) => !plotIds.has(id)),
  ];
  return { draft: { ...draft,
    plant_ids: draft.plant_ids.filter((id) => plantIds.has(id)),
    plot_ids: draft.plot_ids.filter((id) => plotIds.has(id)),
  }, missingIds };
}

// Auth cleanup must invalidate already-open editors as well as remove private text.
export function clearJournalDrafts(): void {
  generation += 1;
  const keys = Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i));
  for (const key of keys) if (key?.startsWith(PREFIX)) localStorage.removeItem(key);
}
