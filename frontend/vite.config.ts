import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    port: 5173,
    proxy: {
      // The browser only ever talks to the Vite origin, so the session cookie is
      // first-party and no CORS preflight is needed in development.
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          maplibre: ["maplibre-gl"],   // ~800kb: keep it out of the landing-page bundle
          charts: ["recharts"],
        },
      },
    },
  },
});
