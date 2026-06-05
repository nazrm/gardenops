const DEFAULT_APP_NAME = "GardenOps";
const DEFAULT_APP_SLUG = "gardenops";

function cleanName(value: unknown): string {
  if (typeof value !== "string") return "";
  return value
    .replace(/[\x00-\x1f\x7f]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80)
    .trim();
}

function slugify(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60)
    .replace(/-+$/g, "");
}

export const APP_NAME = cleanName(import.meta.env.VITE_APP_NAME) || DEFAULT_APP_NAME;
export const APP_SLUG =
  slugify(cleanName(import.meta.env.VITE_APP_SLUG)) ||
  slugify(APP_NAME) ||
  DEFAULT_APP_SLUG;

export function appName(): string {
  return APP_NAME;
}

export function appSlug(): string {
  return APP_SLUG;
}
