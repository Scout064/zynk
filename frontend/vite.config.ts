import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/docs": "http://localhost:8000",
      "/redoc": "http://localhost:8000",
      "/openapi.json": "http://localhost:8000",
    },
  },
});
