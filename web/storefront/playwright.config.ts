import { defineConfig, devices } from "@playwright/test";

// The dev server and the agent service are started by hand (see the repo README); these
// tests drive the real commercetools project and the real Stripe connector in test mode,
// so they are deliberately not parallel and not retried -- each run creates a real order.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 180_000,
  reporter: "list",
  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:3005",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
