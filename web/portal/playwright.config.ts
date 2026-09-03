import { defineConfig, devices } from "@playwright/test";

// Drives the real commercetools project. Not parallel and not retried: approving a change
// here writes to the platform.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 180_000,
  reporter: "list",
  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:3105",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
