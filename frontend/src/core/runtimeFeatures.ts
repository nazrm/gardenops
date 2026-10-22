export const shadeMapBrowserEnabled = typeof __SHADEMAP_BROWSER_ENABLED__ !== "undefined"
  ? __SHADEMAP_BROWSER_ENABLED__
  : ["1", "true", "yes", "on"].includes(import.meta.env["VITE_SHADEMAP_ENABLED"] ?? "false");
