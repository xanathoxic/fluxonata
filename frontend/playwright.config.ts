import { defineConfig } from "@playwright/test";

// Requires the dev stack: scripts/dev-up.sh (API + worker + web, demo data).
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  expect: { timeout: 15_000 }, // machine chargée (CI complète) : 5 s par défaut rendaient certains tests intermittents
  timeout: 120_000, // des exécutions réelles démarrent des conteneurs : la machine de dév peut être chargée
  reporter: [["list"]],
  use: { baseURL: `http://localhost:${process.env.ORCH_WEB_PORT ?? "5173"}`, locale: "fr-FR", trace: "retain-on-failure" },
});
