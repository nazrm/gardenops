// frontend/src/core/featureGates.ts
/**
 * Feature gate utility — controls which features are visible/accessible
 * based on the user's subscription tier.
 */

let _allowedFeatures: ReadonlySet<string> = new Set();
let _tier: string = "home";

/** Called once after /api/auth/me responds. */
export function setFeatureGates(
  tier: string,
  features: string[],
): void {
  _tier = tier;
  _allowedFeatures = new Set(features);
}

/** Check if a feature is enabled for the current user. */
export function isFeatureEnabled(feature: string): boolean {
  return _allowedFeatures.has(feature);
}

/** Get the current subscription tier. */
export function currentTier(): string {
  return _tier;
}

/** Get all allowed feature keys. */
export function allowedFeatures(): ReadonlySet<string> {
  return _allowedFeatures;
}

/** Static tier→features map for downgrade confirmation dialogs. Cumulative. */
const TIER_FEATURES: Record<string, readonly string[]> = {
  home: [
    "map", "plots", "plants", "journal", "harvest_basic",
    "onboarding", "media", "theme", "snapshots", "exports_basic",
  ],
  enthusiast: [
    "map", "plots", "plants", "journal", "harvest_basic",
    "onboarding", "media", "theme", "snapshots", "exports_basic",
    "tasks", "issues", "weather", "notifications", "shade_map",
    "planner", "saved_views", "statistics", "inventory", "care", "calendar",
    "calendar_subscriptions", "exports_full",
  ],
  pro: [
    "map", "plots", "plants", "journal", "harvest_basic",
    "onboarding", "media", "theme", "snapshots", "exports_basic",
    "tasks", "issues", "weather", "notifications", "shade_map",
    "planner", "saved_views", "statistics", "inventory", "care", "calendar",
    "calendar_subscriptions", "exports_full",
    "multi_garden", "user_management", "mfa", "procurement", "workflows",
    "ai", "email_notifications", "audit", "admin_panel", "api_key_access",
  ],
};

/** Features available in fromTier but not in toTier. For downgrade dialogs. */
export function featuresLostOnDowngrade(
  fromTier: string,
  toTier: string,
): string[] {
  const from = new Set(TIER_FEATURES[fromTier] ?? []);
  const to = new Set(TIER_FEATURES[toTier] ?? []);
  return [...from].filter((f) => !to.has(f));
}
