import { describe, expect, it } from "vitest";
import { VoiceController } from "./voice-controller";
import type { SpeechRecognitionAdapter, VoiceErrorCode } from "./types";

function recognition() {
  let result:
    ((value: { transcript: string; isFinal: boolean }) => void) | undefined;
  let end: (() => void) | undefined;
  let error: ((value: VoiceErrorCode) => void) | undefined;
  const adapter: SpeechRecognitionAdapter & {
    emit(text: string, final: boolean): void;
    finish(): void;
    starts: number;
    aborts: number;
  } = {
    starts: 0,
    aborts: 0,
    start: () => {
      adapter.starts += 1;
    },
    stop: () => end?.(),
    abort: () => {
      adapter.aborts += 1;
    },
    onResult: (listener) => {
      result = listener;
      return () => undefined;
    },
    onEnd: (listener) => {
      end = listener;
      return () => undefined;
    },
    onError: (listener) => {
      error = listener;
      return () => undefined;
    },
    dispose: () => undefined,
    emit: (text, final) => result?.({ transcript: text, isFinal: final }),
    finish: () => end?.(),
  };
  return { adapter, fail: (code: VoiceErrorCode) => error?.(code) };
}

function harness() {
  const rec = recognition();
  const submitted: string[] = [];
  const controller = new VoiceController({
    recognition: rec.adapter,
    language: () => "en-US",
    submit: async ({ text }) => {
      submitted.push(text);
    },
    timers: { set: () => 1, clear: () => undefined },
  });
  return { controller, rec, submitted };
}

describe("VoiceController", () => {
  it("submits one push-to-talk final transcript", async () => {
    const h = harness();
    h.controller.pressToTalk();
    h.rec.adapter.emit("hello Friday", true);
    h.controller.releaseToTalk();
    h.rec.adapter.finish();
    await Promise.resolve();
    expect(h.submitted).toEqual(["hello Friday"]);
  });
  it("never submits a key release without a matching press", async () => {
    const h = harness();
    h.controller.releaseToTalk();
    h.rec.adapter.finish();
    await Promise.resolve();
    expect(h.submitted).toEqual([]);
  });
  it("treats permission denial as a voice-only failure", () => {
    const h = harness();
    h.controller.setHandsFree(true);
    h.rec.fail("not-allowed");
    expect(h.controller.snapshot()).toMatchObject({
      state: "error",
      handsFree: false,
      error: "not-allowed",
    });
  });
  it("barge-in cannot submit or approve anything", () => {
    const h = harness();
    h.controller.speakingStarted();
    h.controller.bargeIn();
    expect(h.submitted).toEqual([]);
    expect(h.controller.snapshot().state).toBe("idle");
  });
  it("ignores adapter events after disposal", async () => {
    const h = harness();
    h.controller.pressToTalk();
    h.controller.dispose();
    h.rec.adapter.emit("late", true);
    h.rec.adapter.finish();
    await Promise.resolve();
    expect(h.submitted).toEqual([]);
  });
});
