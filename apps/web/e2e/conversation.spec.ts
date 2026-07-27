import { expect, test } from "@playwright/test";
import { installFakeSpeech } from "./fake-speech";

test("typed conversation creates a durable run and receives its result", async ({
  page,
}) => {
  const url = process.env.FRIDAY_E2E_WEB_URL;
  if (!url) throw new Error("FRIDAY_E2E_WEB_URL was not set by global setup");
  await installFakeSpeech(page);
  await page.goto(url);
  await page.getByLabel("Message").fill("Browser conversational proof");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Browser conversational proof")).toBeVisible();
  // Echoing the prompt only proves the turn was accepted; the answer is what
  // proves the run reached the runtime and its result came back.
  await expect(page.getByText("E2E task completed")).toBeVisible({
    timeout: 30_000,
  });
});
