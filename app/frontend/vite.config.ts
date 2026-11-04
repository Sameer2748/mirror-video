import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 8765 is often the older Downloads copy. This workspace's backend is 8766 (app/config.json).
const api = process.env.DUBSTUDIO_API || "http://127.0.0.1:8766";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: { "/api": { target: api, ws: true } },
  },
});

