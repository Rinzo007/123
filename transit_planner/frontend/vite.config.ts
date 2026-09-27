import { defineConfig } from "vite";

export default defineConfig({
  build: {
    rollupOptions: {
      input: "src/main.ts",
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
