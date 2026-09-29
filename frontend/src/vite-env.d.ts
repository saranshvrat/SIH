/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Override the API base path. Defaults to /api/v1 via the Vite dev proxy. */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
