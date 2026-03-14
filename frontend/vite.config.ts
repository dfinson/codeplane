import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  css: {
    postcss: "./postcss.config.cjs",
  },
  build: {
    outDir: "dist",
  },
  test: {
    exclude: ["e2e/**", "node_modules/**"],
  },
});
