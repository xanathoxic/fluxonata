/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const api = `http://127.0.0.1:${process.env.ORCH_API_PORT ?? "8100"}`;

export default defineConfig({
  plugins: [react()],
  server: { port: Number(process.env.ORCH_WEB_PORT ?? 5173), proxy: { "/api": api } },
  test: { exclude: ["e2e/**", "node_modules/**"], environment: "jsdom", globals: true, setupFiles: ["./src/test-setup.ts"] },
});
