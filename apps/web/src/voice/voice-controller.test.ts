import { describe, expect, it } from "vitest";
import { MAX_HANDS_FREE_REARMS } from "./constants";
import { VoiceController, type TimerApi } from "./voice-controller";
import type { SpeechRecognitionAdapter, VoiceErrorCode } from "./types";

function recognition() {
  let result:
    | ((value: {
        attempt: number;
        transcript: string;
        isFinal: boolean;
      }) => void)
    | undefined;
  let end: ((attempt: number) => void) | undefined;
  let error: ((attempt: number, value: VoiceErrorCode) => void) | undefined;
  let latestAttempt = 0;
  const adapter: SpeechRecognitionAdapter & {
    emit(text: string, final: boolean): void;
    finish(): void;
    starts: number;
    aborts: number;
  } = {
    starts: 0,
    aborts: 0,
    start: ({ attempt }) => {
      adapter.starts += 1;
      latestAttempt = attempt;
    },
    stop: () => end?.(latestAttempt),
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
    emit: (text, final) =>
      result?.({ attempt: latestAttempt, transcript: text, isFinal: final }),
    finish: () => end?.(latestAttempt),
  };
  return {
    adapter,
    emit: (text: string, final: boolean, attempt = latestAttempt) =>
      result?.({ attempt, transcript: text, isFinal: final }),
    fail: (code: VoiceErrorCode, attempt = latestAttempt) =>
      error?.(attempt, code),
    finish: (attempt = latestAttempt) => end?.(attempt),
  };
}

/** Controllable stand-in for setTimeout, so silence-window behaviour is
 * asserted on which timer is live rather than on wall-clock delays. */
function timers() {
  const live = new Map<number, () => void>();
  const cleared: number[] = [];
  let nextId = 1;
  return {
    api: {
      set(callback: () => void) {
        const id = nextId;
        nextId += 1;
        live.set(id, callback);
        return id;
      },
      clear(id: number) {
        if (live.delete(id)) cleared.push(id);
      },
    },
    cleared,
    armed: () => [...live.keys()],
    fire() {
      const next = [...live.entries()][0];
      if (!next) throw new Error("no silence timer is armed");
      live.delete(next[0]);
      next[1]();
    },
  };
}

function harness(
  timerApi: TimerApi = { set: () => 1, clear: () => undefined },
) {
  const rec = recognition();
  const submitted: string[] = [];
  const output = { stops: 0, stop: () => output.stops++ };
  const audioLevel = {
    starts: 0,
    start: async () => {
      audioLevel.starts += 1;
    },
    stop: () => undefined,
    onSustainedSpeech: () => () => undefined,
  };
  /** Holds a submission open so an interrupt can land while it is in flight. */
  let hold = false;
  let release: (() => void) | null = null;
  const controller = new VoiceController({
    recognition: rec.adapter,
    output,
    audioLevel,
    language: () => "en-US",
    submit: async ({ text }) => {
      submitted.push(text);
      if (hold) await new Promise<void>((resolve) => (release = resolve));
    },
    timers: timerApi,
  });
  return {
    controller,
    rec,
    submitted,
    output,
    audioLevel,
    holdSubmit() {
      hold = true;
    },
    releaseSubmit() {
      hold = false;
      release?.();
      release = null;
    },
  };
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
  it("barge-in stops output before recognition can resume", () => {
    const h = harness();
    h.controller.speakingStarted();
    h.controller.bargeIn();
    expect(h.submitted).toEqual([]);
    expect(h.output.stops).toBe(1);
    expect(h.rec.adapter.aborts).toBeGreaterThan(0);
    expect(h.controller.snapshot().state).toBe("idle");
  });
  it("push-to-talk cancels active speech before starting recognition", () => {
    const h = harness();
    h.controller.speakingStarted();
    h.controller.pressToTalk();
    expect(h.output.stops).toBe(1);
    expect(h.rec.adapter.starts).toBe(1);
  });
  it("does not start a new microphone session while a run is processing", () => {
    const h = harness();
    h.controller.notifyProcessing();

    h.controller.pressToTalk();
    h.controller.setHandsFree(true);

    expect(h.rec.adapter.starts).toBe(0);
    expect(h.controller.snapshot()).toMatchObject({
      state: "processing",
      handsFree: false,
    });
  });
  it("restarts the silence window on interim speech after a final result", async () => {
    const clock = timers();
    const h = harness(clock.api);
    h.controller.setHandsFree(true);
    h.rec.adapter.emit("hãy kiểm tra", true);
    const armedAfterFinal = clock.armed();
    expect(armedAfterFinal).toHaveLength(1);

    h.rec.adapter.emit("email", false);

    // The window the final result armed is gone, replaced by a fresh one, so
    // it cannot finalize while the user is still mid-sentence.
    expect(clock.cleared).toContain(armedAfterFinal[0]);
    expect(clock.armed()).toHaveLength(1);
    expect(clock.armed()[0]).not.toBe(armedAfterFinal[0]);
    expect(h.submitted).toEqual([]);

    clock.fire();
    await Promise.resolve();
    expect(h.submitted).toEqual(["hãy kiểm tra"]);
  });
  it("keeps listening when the silence window elapses with nothing to submit", async () => {
    const clock = timers();
    const h = harness(clock.api);
    h.controller.setHandsFree(true);
    h.rec.adapter.emit("uh", false);

    clock.fire();
    await Promise.resolve();

    expect(h.submitted).toEqual([]);
    expect(h.rec.adapter.starts).toBe(1);
    expect(h.controller.snapshot()).toMatchObject({
      handsFree: true,
      state: "listening",
    });
  });
  it("recovers hands-free when the recognizer ends on its own", () => {
    const h = harness();
    h.controller.setHandsFree(true);
    expect(h.rec.adapter.starts).toBe(1);

    h.rec.adapter.finish();

    expect(h.rec.adapter.starts).toBe(2);
    expect(h.controller.snapshot()).toMatchObject({
      handsFree: true,
      state: "listening",
    });
  });
  it("waits for an errored attempt to end before rearming exactly once", () => {
    const h = harness();
    h.controller.setHandsFree(true);
    expect(h.rec.adapter.starts).toBe(1);

    h.rec.fail("no-speech", 1);
    expect(h.rec.adapter.starts).toBe(1);

    h.rec.finish(1);
    expect(h.rec.adapter.starts).toBe(2);

    // A duplicate/late terminal callback for A must not churn B.
    h.rec.finish(1);
    expect(h.rec.adapter.starts).toBe(2);
    expect(h.controller.snapshot()).toMatchObject({
      handsFree: true,
      state: "listening",
    });
  });
  it("ignores stale error and end callbacks after aborting A and starting B", () => {
    const h = harness();
    h.controller.setHandsFree(true);
    h.controller.speakingStarted(); // aborts attempt A
    h.controller.bargeIn(); // starts attempt B
    expect(h.rec.adapter.starts).toBe(2);

    h.rec.emit("late speech", true, 1);
    h.rec.fail("aborted", 1);
    h.rec.finish(1);

    expect(h.rec.adapter.starts).toBe(2);
    expect(h.controller.snapshot()).toMatchObject({
      handsFree: true,
      state: "listening",
    });
  });
  it("submits speech captured before an end it never asked for", async () => {
    const h = harness();
    h.controller.setHandsFree(true);
    h.rec.adapter.emit("check my email", true);

    h.rec.adapter.finish();
    await Promise.resolve();

    expect(h.submitted).toEqual(["check my email"]);
  });
  it("gives up on hands-free once the recognizer keeps dying", () => {
    const h = harness();
    h.controller.setHandsFree(true);
    for (let attempt = 0; attempt <= MAX_HANDS_FREE_REARMS; attempt += 1)
      h.rec.adapter.finish();

    expect(h.rec.adapter.starts).toBe(1 + MAX_HANDS_FREE_REARMS);
    expect(h.controller.snapshot()).toMatchObject({
      handsFree: false,
      state: "error",
      error: "unknown",
    });
  });
  it("never restarts recognition while Friday is speaking", () => {
    const h = harness();
    h.controller.setHandsFree(true);
    const startsBeforeSpeaking = h.rec.adapter.starts;

    h.controller.speakingStarted();
    // The recognizer reports the abort the controller itself requested.
    h.rec.fail("aborted");
    h.rec.adapter.finish();

    expect(h.rec.adapter.starts).toBe(startsBeforeSpeaking);
    expect(h.controller.snapshot().state).not.toBe("listening");
  });
  it("stops listening for good when hands-free is switched off", () => {
    const h = harness();
    h.controller.setHandsFree(true);
    h.controller.setHandsFree(false);
    const startsAfterOff = h.rec.adapter.starts;

    h.rec.adapter.finish();

    expect(h.rec.adapter.starts).toBe(startsAfterOff);
    expect(h.controller.snapshot()).toMatchObject({
      handsFree: false,
      state: "idle",
    });
  });
  it("never opens the microphone to speak a typed-only answer", () => {
    const h = harness();
    // No push-to-talk, no hands-free — the user has only ever typed.
    h.controller.speakingStarted();
    expect(h.audioLevel.starts).toBe(0);
  });
  it("arms barge-in monitoring only for a hands-free session", () => {
    const h = harness();
    h.controller.setHandsFree(true);
    h.controller.speakingStarted();
    expect(h.audioLevel.starts).toBe(1);
  });
  it("does not resume processing when a submission resolves after an interrupt", async () => {
    const h = harness();
    h.holdSubmit();
    h.controller.pressToTalk();
    h.rec.adapter.emit("cancel me", true);
    h.controller.releaseToTalk();
    h.rec.adapter.finish();
    await Promise.resolve();
    expect(h.controller.snapshot().state).toBe("submitting");

    h.controller.interrupt();
    expect(h.controller.snapshot().state).toBe("idle");

    // The server answers the request the user already walked away from.
    h.releaseSubmit();
    await Promise.resolve();
    await Promise.resolve();

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
