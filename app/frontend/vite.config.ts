import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// dev: `npm run dev` on 5173 talks to the backend on 8765
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": { target: "http://localhost:8765", ws: true } } },
});
