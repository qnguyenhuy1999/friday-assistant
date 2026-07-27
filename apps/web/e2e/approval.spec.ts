import { expect, test } from "@playwright/test";
import { installFakeSpeech } from "./fake-speech";

test("a conversation can approve and finish a protected tool invocation", async ({
  page,
}) => {
  const url = process.env.FRIDAY_E2E_WEB_URL;
  if (!url) throw new Error("FRIDAY_E2E_WEB_URL was not set by global setup");
  await installFakeSpeech(page);
  await page.goto(url);
  await page.getByLabel("Message").fill("E2E approval proof");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(
    page.getByText("Approval required", { exact: true }),
  ).toBeVisible({
    timeout: 30_000,
  });
  await page.getByRole("button", { name: "Review approval" }).click();
  await page.getByRole("button", { name: /pending/ }).click();
  await page.getByLabel("Your name or email").fill("E2E reviewer");
  await page.getByRole("button", { name: "Approve" }).click();

  await page.getByRole("button", { name: "Back to run" }).click();
  await page.getByRole("button", { name: "Conversation" }).click();
  await expect(page.getByText("E2E approval task completed")).toBeVisible({
    timeout: 30_000,
  });
});
