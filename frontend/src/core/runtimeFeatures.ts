const FALSE_VALUES = new Set(["0", "false", "no", "off"]);

export const shadeMapBrowserEnabled = !FALSE_VALUES.has(
  import.meta.env["VITE_SHADEMAP_ENABLED"]?.trim().toLowerCase() ?? "true",
);
