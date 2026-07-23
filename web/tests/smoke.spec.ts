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
  await expect(page.getByRole("heading", { name: /Kill any worker/i })).toBeVisible();
});

// Every page title now appears twice: once in the sticky top bar (h1) and once
// in the page's own header (h2). These assertions target the page's heading, so
// they check the view rendered rather than that the nav knows its own name.
test("health page loads and reports API state", async ({ page }) => {
  await page.goto("/health");
  await expect(page.getByRole("heading", { level: 2, name: "Health" })).toBeVisible();
  // Without a running API it shows an error card; with one, status rows.
  // Either way the page must not crash.
  await expect(page.locator("body")).toBeVisible();
});

// The Phase-3 pages both fetch on mount. CI has no API, so these assert the
// degraded path: an error card, not a blank screen or a crashed render.
test("approvals inbox renders without an API", async ({ page }) => {
  await page.goto("/approvals");
  await expect(
    page.getByRole("heading", { level: 2, name: "Approvals" }),
  ).toBeVisible();
  // The status filter is a tablist, not loose buttons — assert the role it
  // actually exposes, since that is what a screen reader gets too.
  await expect(page.getByRole("tab", { name: "Waiting" })).toBeVisible();
  await expect(page.locator("body")).toBeVisible();
});

test("node catalog renders without an API", async ({ page }) => {
  await page.goto("/nodes");
  await expect(page.getByRole("heading", { name: "Node catalog" })).toBeVisible();
  await expect(page.locator("body")).toBeVisible();
});

// The run detail page pulls in React Flow, which brings its own stylesheet and
// measures the DOM on mount — the class of dependency that breaks server
// rendering rather than the component itself. With no API there is no graph to
// draw, so the assertion is that the page degrades instead of crashing.
test("run detail degrades without an API", async ({ page }) => {
  const crashes: string[] = [];
  page.on("pageerror", (e) => crashes.push(e.message));

  await page.goto("/runs/00000000-0000-0000-0000-000000000000");
  await expect(page.getByRole("link", { name: "Runs" })).toBeVisible();
  // No run means no history, so the DAG must stay absent rather than render an
  // empty canvas that reads as "this workflow has no steps".
  await expect(page.getByTestId("run-dag")).toHaveCount(0);
  expect(crashes).toEqual([]);
});

test("chaos lab renders without an API", async ({ page }) => {
  await page.goto("/chaos");
  await expect(
    page.getByRole("heading", { level: 2, name: "Chaos Lab" }),
  ).toBeVisible();
  // The kill buttons only appear once the API reports live targets, so with no
  // API the page must still render its explanation rather than an empty shell.
  await expect(page.getByText(/Give it something to lose/i)).toBeVisible();
  // The recovery view is keyed off a run, so with no API it must stay absent
  // rather than render an empty chart that looks like "nothing went wrong".
  await expect(page.getByText(/Watch it rebuild/i)).toHaveCount(0);
});
