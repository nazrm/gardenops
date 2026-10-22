/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_APP_NAME?: string;
  readonly VITE_APP_SLUG?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
declare const __SHADEMAP_BROWSER_ENABLED__: boolean;
