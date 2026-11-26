import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  css: {
    postcss: "./postcss.config.cjs",
  },
  build: {
    outDir: "../backend/web",
    emptyOutDir: true,
    chunkSizeWarningLimit: 750,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) {
            return undefined;
          }

          if (id.includes("node_modules/monaco-editor") || id.includes("node_modules/@monaco-editor/")) {
            return "monaco";
          }

          if (id.includes("node_modules/@xterm/")) {
            return "terminal-vendor";
          }

          if (id.includes("node_modules/react-syntax-highlighter") || id.includes("node_modules/refractor") || id.includes("node_modules/prismjs")) {
            return "syntax-highlighter";
          }

          return undefined;
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8080",
        ws: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/setupTests.ts"],
    exclude: ["e2e/**", "tracker-e2e/**", "recipe-e2e/**", "node_modules/**"],
  },
});
