import { expect, test } from "@playwright/test";

test("a real browser receives a durable deterministic agent result", async ({
  page,
}) => {
  await page.goto("/");
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
