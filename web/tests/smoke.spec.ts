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
