import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Pipeline runs several minutes; default proxy timeout drops the connection → browser "Failed to fetch"
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        timeout: 0,
        proxyTimeout: 0,
      },
    },
  },
});
