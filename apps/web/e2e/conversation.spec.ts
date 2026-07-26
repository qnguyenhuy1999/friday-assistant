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
});

test("voice permission denial leaves typed conversation available", async ({
  page,
}) => {
  const url = process.env.FRIDAY_E2E_WEB_URL;
  if (!url) throw new Error("FRIDAY_E2E_WEB_URL was not set by global setup");
  await installFakeSpeech(page, true);
  await page.goto(url);
  await page.getByRole("button", { name: "Hold to talk" }).click();
  await page.getByLabel("Message").fill("Typed fallback");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Typed fallback")).toBeVisible();
});
