import { expect, test } from "@playwright/test";

test("dashboard shell renders with nav and dark theme", async ({ page }) => {
  await page.goto("/");

  // Dark-first: the root element carries the `dark` class.
  await expect(page.locator("html")).toHaveClass(/dark/);

  // Brand + primary nav are present.
  await expect(page.getByText("Ancora").first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Dashboard" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Workflows" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Health" })).toBeVisible();

  // Dashboard heading renders.
  await expect(
    page.getByRole("heading", { name: /Kill any worker/i }),
  ).toBeVisible();
});

test("health page loads and reports API state", async ({ page }) => {
  await page.goto("/health");
  await expect(
    page.getByRole("heading", { name: "API Health" }),
  ).toBeVisible();
  // Without a running API it shows an error card; with one, status rows.
  // Either way the page must not crash.
  await expect(page.locator("body")).toBeVisible();
});

// The Phase-3 pages both fetch on mount. CI has no API, so these assert the
// degraded path: an error card, not a blank screen or a crashed render.
test("approvals inbox renders without an API", async ({ page }) => {
  await page.goto("/approvals");
  await expect(page.getByRole("heading", { name: "Approvals" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Waiting" })).toBeVisible();
  await expect(page.locator("body")).toBeVisible();
});

test("node catalog renders without an API", async ({ page }) => {
  await page.goto("/nodes");
  await expect(page.getByRole("heading", { name: "Node catalog" })).toBeVisible();
  await expect(page.locator("body")).toBeVisible();
});

test("chaos lab renders without an API", async ({ page }) => {
  await page.goto("/chaos");
  await expect(page.getByRole("heading", { name: "Chaos Lab" })).toBeVisible();
  // The kill buttons only appear once the API reports live targets, so with no
  // API the page must still render its explanation rather than an empty shell.
  await expect(page.getByText(/Give it something to lose/i)).toBeVisible();
});
