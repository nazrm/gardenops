import { defineConfig } from "vite";
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

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(appVersion()),
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
      "/api": "http://localhost:8000",
    },
  },
});
