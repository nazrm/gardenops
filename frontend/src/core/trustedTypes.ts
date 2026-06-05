/**
 * Trusted Types is intentionally configured without a permissive default policy.
 *
 * The app's reviewed HTML sinks go through the `gardenops-html` policy created in
 * `core/sanitize.ts`. This module also installs a tightly scoped `default`
 * policy for FullCalendar's internal static templates, because the library
 * writes two fixed shell fragments via `innerHTML` during render. Everything
 * else still fails closed.
 */

type TrustedHtmlPolicy = {
  createHTML: (input: string) => unknown;
};

type TrustedTypesFactory = {
  createPolicy: (
    name: string,
    rules: { createHTML: (input: string) => string },
  ) => TrustedHtmlPolicy;
  getPolicy?: (name: string) => TrustedHtmlPolicy | null;
};

const TRUSTED_STATIC_LIBRARY_HTML = new Set([
  "<table><tr><td><div></div></td></tr></table>",
  "<div></div>",
]);

function installLibraryTemplatePolicy(): void {
  const trustedTypesFactory = (
    window as Window & { trustedTypes?: TrustedTypesFactory }
  ).trustedTypes;
  if (!trustedTypesFactory) return;
  if (trustedTypesFactory.getPolicy?.("default")) return;
  try {
    trustedTypesFactory.createPolicy("default", {
      createHTML: (input) => {
        if (TRUSTED_STATIC_LIBRARY_HTML.has(input)) {
          return input;
        }
        throw new TypeError("Blocked unreviewed HTML string assignment");
      },
    });
  } catch {
    // Ignore environments where a default policy already exists or TT is unavailable.
  }
}

installLibraryTemplatePolicy();
