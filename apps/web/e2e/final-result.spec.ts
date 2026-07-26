import { expect, test } from "@playwright/test";

test("a real browser receives a durable deterministic agent result", async ({
  page,
}) => {
  const webUrl = process.env.FRIDAY_E2E_WEB_URL;
  if (!webUrl)
    throw new Error("FRIDAY_E2E_WEB_URL was not set by global setup");
  await page.goto(webUrl);
  await page.getByLabel("Title").fill("Browser-to-runtime proof");
  await page.getByRole("button", { name: "Create task" }).click();
  await page.getByRole("button", { name: "Start run" }).click();

  await expect(page.getByText("Status: succeeded")).toBeVisible();
  await expect(page.getByRole("status")).toContainText("Run succeeded.");
  await expect(page.getByRole("status")).toContainText("E2E task completed");
  await expect(page.getByRole("status")).toContainText(
    "deterministic-e2e-brain",
  );
});
