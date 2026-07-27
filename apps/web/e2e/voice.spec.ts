import { expect, test, type Page } from "@playwright/test";
import { BARGE_IN_BASELINE_SAMPLES } from "../src/voice/constants";
import { installFakeSpeech } from "./fake-speech";

/** The deterministic E2E brain always finishes with this summary, so it is
 * what a completed turn must render and what TTS must be handed. */
const ANSWER = "E2E task completed";

function url(): string {
  const value = process.env.FRIDAY_E2E_WEB_URL;
  if (!value) throw new Error("FRIDAY_E2E_WEB_URL was not set by global setup");
  return value;
}

async function open(page: Page, denyMicrophone = false): Promise<void> {
  await installFakeSpeech(page, denyMicrophone);
  await page.goto(url());
  // The conversation is created on mount, and every input is held back until
  // it exists — so an enabled control is the proof the page is ready.
  await expect(page.getByLabel("Message")).toBeEnabled();
  if (!denyMicrophone)
    await expect(
      page.getByRole("button", { name: "Hold to talk" }),
    ).toBeEnabled();
}

/** Speaks one push-to-talk utterance through the real recognition adapter:
 * press, final result, release. Release stops the recognizer, whose end event
 * is what submits the turn. */
async function sayPushToTalk(page: Page, text: string): Promise<void> {
  const button = page.getByRole("button", { name: "Hold to talk" });
  const before = await recognitionStarts(page);
  // The component uses onPointerDown/onPointerUp (not mouse events), so we
  // must dispatch real PointerEvent objects with the properties the handler
  // checks: isPrimary, button, pointerId.
  await button.evaluate((el) =>
    el.dispatchEvent(
      new PointerEvent("pointerdown", {
        isPrimary: true,
        button: 0,
        pointerId: 1,
        bubbles: true,
        cancelable: true,
      }),
    ),
  );
  // The result has to land on a live session, so wait for the press to open one.
  await expect.poll(() => recognitionStarts(page)).toBeGreaterThan(before);
  await page.evaluate((value) => window.__fakeSpeech.result(value), text);
  await button.evaluate((el) =>
    el.dispatchEvent(
      new PointerEvent("pointerup", {
        isPrimary: true,
        button: 0,
        pointerId: 1,
        bubbles: true,
        cancelable: true,
      }),
    ),
  );
}

const spoken = (page: Page) =>
  page.evaluate(() => window.__fakeSpeech.spoken());
const microphoneRequests = (page: Page) =>
  page.evaluate(() => window.__fakeSpeech.microphoneRequests());
const recognitionStarts = (page: Page) =>
  page.evaluate(() => window.__fakeSpeech.recognitionStarts());
const cancels = (page: Page) =>
  page.evaluate(() => window.__fakeSpeech.cancels());

test("a spoken turn reaches the runtime and its answer is spoken back", async ({
  page,
}) => {
  await open(page);
  await sayPushToTalk(page, "Spoken conversational proof");

  // The transcript proves the durable turn, the answer proves the run
  // completed, and `spoken` proves the answer reached synthesis.
  await expect(page.getByText("Spoken conversational proof")).toBeVisible();
  await expect(page.getByText(ANSWER)).toBeVisible();
  await expect.poll(() => spoken(page)).toContain(ANSWER);
});

test("a typed-only conversation never opens the microphone", async ({
  page,
}) => {
  await open(page);
  await page.getByLabel("Message").fill("Typed conversational proof");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByText("Typed conversational proof")).toBeVisible();
  await expect(page.getByText(ANSWER)).toBeVisible();
  // Speaking the answer must not arm barge-in monitoring: the user never
  // opted into voice input, so nothing may request the microphone.
  await expect.poll(() => spoken(page)).toContain(ANSWER);
  expect(await microphoneRequests(page)).toBe(0);
});

test("hands-free resumes listening after the answer is spoken", async ({
  page,
}) => {
  await open(page);
  await page.getByLabel("Hands-free").check();
  await expect.poll(() => recognitionStarts(page)).toBeGreaterThan(0);
  const first = await recognitionStarts(page);

  await page.evaluate(() => window.__fakeSpeech.result("Hands free proof"));
  // Silence ends the utterance; the adapter's end event submits it.
  await page.evaluate(() => window.__fakeSpeech.end());

  await expect(page.getByText(ANSWER)).toBeVisible();
  await expect.poll(() => spoken(page)).toContain(ANSWER);
  // The session must re-arm on its own, otherwise hands-free dies after one turn.
  await expect.poll(() => recognitionStarts(page)).toBeGreaterThan(first);
});

test("stale terminal callbacks do not disturb a rearmed hands-free attempt", async ({
  page,
}) => {
  await open(page);
  await page.getByLabel("Hands-free").check();
  await expect.poll(() => recognitionStarts(page)).toBe(1);

  // A benign error is terminal, but the controller must wait for this
  // attempt's end before it starts its replacement.
  await page.evaluate(() => window.__fakeSpeech.error("no-speech"));
  expect(await recognitionStarts(page)).toBe(1);
  await page.evaluate(() => window.__fakeSpeech.end());
  await expect.poll(() => recognitionStarts(page)).toBe(2);

  // Browser callbacks from A can arrive after B is live. They must be fenced
  // by the attempt that created the BrowserRecognition instance.
  await page.evaluate(() => {
    window.__fakeSpeech.staleResult("late speech");
    window.__fakeSpeech.staleError("aborted");
    window.__fakeSpeech.staleEnd();
  });
  await page.waitForTimeout(50);
  expect(await recognitionStarts(page)).toBe(2);
});

test("sustained speech during playback barges in and returns to listening", async ({
  page,
}) => {
  await open(page);
  // Hold the utterance open so the user can speak over it.
  await page.evaluate(() => window.__fakeSpeech.holdSpeech());
  await page.getByLabel("Hands-free").check();
  await page.evaluate(() => window.__fakeSpeech.result("Barge in proof"));
  await page.evaluate(() => window.__fakeSpeech.end());

  await expect(page.getByText("Speaking…")).toBeVisible();
  // Only a hands-free session may monitor the microphone, and it must actually
  // do so — barge-in is what makes an interrupted answer stoppable by voice.
  await expect.poll(() => microphoneRequests(page)).toBeGreaterThan(0);
  const before = await recognitionStarts(page);
  const cancelsBefore = await cancels(page);

  // The monitor calibrates its threshold from its first samples, so the quiet
  // baseline has to be in before the user speaks over the answer.
  await expect
    .poll(() => page.evaluate(() => window.__fakeSpeech.inputSamples()))
    .toBeGreaterThan(BARGE_IN_BASELINE_SAMPLES);
  await page.evaluate(() => window.__fakeSpeech.setInputLevel(60));

  // Barge-in stops the answer and hands the microphone back. Asserting on the
  // cancel and the new session rather than the "Listening…" label, because the
  // silence window can retire that label before an assertion observes it.
  await expect.poll(() => cancels(page)).toBeGreaterThan(cancelsBefore);
  await expect.poll(() => recognitionStarts(page)).toBeGreaterThan(before);
});

test("a failed utterance does not strand the session in speaking", async ({
  page,
}) => {
  await open(page);
  await page.evaluate(() => window.__fakeSpeech.holdSpeech());
  await sayPushToTalk(page, "Synthesis failure proof");

  await expect(page.getByText("Speaking…")).toBeVisible();
  await page.evaluate(() => window.__fakeSpeech.failSpeech());

  // An utterance that errors is terminal: the session has to become usable
  // again rather than waiting forever for an `end` that never comes.
  await expect(page.getByText("Ready")).toBeVisible();
  await expect(page.getByLabel("Message")).toBeEnabled();
});

test("Escape suppresses the answer of the run it interrupted", async ({
  page,
}) => {
  await open(page);
  await sayPushToTalk(page, "Interrupted proof");
  await page.keyboard.press("Escape");

  // The turn stays in the durable transcript, but the interrupted run's answer
  // must never be spoken — whether or not the worker won the cancellation race.
  await expect(page.getByText("Interrupted proof")).toBeVisible();
  await expect(page.getByText("Ready")).toBeVisible();
  await page.waitForTimeout(2_000);
  expect(await spoken(page)).toEqual([]);
});

test("turning off spoken answers keeps the conversation usable", async ({
  page,
}) => {
  await open(page);
  await page.getByLabel("Speak answers").uncheck();
  await sayPushToTalk(page, "Muted proof");

  await expect(page.getByText(ANSWER)).toBeVisible();
  expect(await spoken(page)).toEqual([]);
  // With speech off nothing else reports the answer, so the session would die
  // here if delivery were not reported independently of synthesis.
  await expect(page.getByText("Ready")).toBeVisible();
  await expect(page.getByLabel("Message")).toBeEnabled();
});

test("voice permission denial leaves typed conversation available", async ({
  page,
}) => {
  await open(page, true);
  await page.getByRole("button", { name: "Hold to talk" }).click();
  await page.getByLabel("Message").fill("Typed fallback");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByText("Typed fallback")).toBeVisible();
  await expect(page.getByText(ANSWER)).toBeVisible();
});
