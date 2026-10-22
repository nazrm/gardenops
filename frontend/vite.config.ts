import { defineConfig, loadEnv } from "vite";
import { readFileSync } from "fs";
import { resolve } from "path";

type FrontendPackageMeta = {
  version?: string;
};

function appVersion(): string {
  try {
    const pkg = JSON.parse(
      readFileSync(resolve(__dirname, "package.json"), "utf-8"),
    ) as FrontendPackageMeta;
    return pkg.version?.trim() || "unknown";
  } catch {
    return "unknown";
  }
}

const apiProxyTarget = process.env["GARDENOPS_VITE_PROXY_TARGET"] || "http://localhost:8000";

export default defineConfig(({ mode }) => {
  const shadeMapEnabled = ["1", "true", "yes", "on"].includes(
    (process.env["VITE_SHADEMAP_ENABLED"]
      ?? loadEnv(mode, process.cwd(), "VITE_")["VITE_SHADEMAP_ENABLED"]
      ?? "false").trim().toLowerCase(),
  );
  return {
    resolve: {
      alias: shadeMapEnabled ? [] : [{
        find: "./components/shadePanel",
        replacement: resolve(__dirname, "src/components/shadePanelDisabled.ts"),
      }],
    },
    define: {
      __APP_VERSION__: JSON.stringify(appVersion()),
      __SHADEMAP_BROWSER_ENABLED__: JSON.stringify(shadeMapEnabled),
    },
    build: {
      sourcemap: false,
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (id.includes("/node_modules/leaflet/")) {
              return "leaflet-vendor";
            }
            return undefined;
          },
        },
      },
    },
    server: {
      proxy: {
        "/api": apiProxyTarget,
        "/calendar/subscriptions": apiProxyTarget,
      },
    },
  };
});
