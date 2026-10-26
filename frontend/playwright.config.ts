import { defineConfig } from "@playwright/test";

// Requires the dev stack: scripts/dev-up.sh (API + worker + web, demo data).
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000, // des exécutions réelles démarrent des conteneurs : la machine de dév peut être chargée
  reporter: [["list"]],
  use: { baseURL: `http://localhost:${process.env.ORCH_WEB_PORT ?? "5173"}`, locale: "fr-FR", trace: "retain-on-failure" },
});
