import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

test("a real browser receives a durable deterministic agent result", async ({
  page,
}) => {
  const webUrl = process.env.FRIDAY_E2E_WEB_URL;
  if (!webUrl)
    throw new Error("FRIDAY_E2E_WEB_URL was not set by global setup");

  const taskTitle = `Browser-to-runtime proof ${randomUUID()}`;

  await page.goto(webUrl);
  // Conversation is the primary screen; Tasks remains an operational surface.
  await page.getByRole("button", { name: "Tasks" }).click();
  await page.getByLabel("Title").fill(taskTitle);
  await page.getByRole("button", { name: "Create task" }).click();

  // The Playwright suite shares one durable database and runs specs in
  // parallel. Target the exact task created by this attempt instead of
  // depending on API ordering or stale rows left by another spec/retry.
  const taskRow = page.getByRole("listitem").filter({ hasText: taskTitle });
  await expect(taskRow).toBeVisible();
  await taskRow.getByRole("button", { name: "Start run" }).click();

  await expect(page.getByText("Status: succeeded")).toBeVisible();
  await expect(page.getByRole("status")).toContainText("Run succeeded.");
  await expect(page.getByRole("status")).toContainText("E2E task completed");
  await expect(page.getByRole("status")).toContainText(
    "deterministic-e2e-brain",
  );
});
