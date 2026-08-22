import { expect, test } from "@playwright/test";

test("an operator creates, activates, and reloads an immutable Agent revision", async ({
  page,
}) => {
  const url = process.env.FRIDAY_E2E_WEB_URL;
  if (!url) throw new Error("FRIDAY_E2E_WEB_URL was not set by global setup");
  await page.goto(url);
  await page.getByRole("button", { name: "Agents" }).click();
  await page.getByLabel("Key").fill("e2e-coding-agent");
  await page.getByLabel("Display name").fill("E2E Coding Agent");
  await page
    .getByLabel("Description")
    .fill("A durable browser operator proof.");
  await page.getByRole("button", { name: "Create agent" }).click();

  await expect(
    page.getByRole("heading", { name: "E2E Coding Agent" }),
  ).toBeVisible();
  await page
    .getByLabel("Instructions")
    .fill("Finish the assigned task safely.");
  await page.getByLabel("Runtime kind").fill("claude_cli");
  await page.getByLabel("Runtime configuration (JSON object)").fill("{}");
  await page.getByRole("button", { name: "Create immutable revision" }).click();
  await expect(page.getByRole("status")).toHaveText(/Created revision v1/);

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Activate v1" }).click();
  await expect(page.getByText("v1 — active")).toBeVisible();
  await page.reload();
  await expect(page.getByText("v1 — active")).toBeVisible();
});
