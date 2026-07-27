import { describe, expect, it } from "vitest";
import { TtsQueue } from "./tts-queue";
import type {
  SpeakOptions,
  SpeechSynthesisAdapter,
  SpeechSynthesisCallbacks,
} from "./types";

const options: SpeakOptions = {
  text: "answer",
  voiceURI: null,
  rate: 1,
  lang: "en-US",
};

function synthesis() {
  const callbacks: SpeechSynthesisCallbacks[] = [];
  const adapter: SpeechSynthesisAdapter = {
    speak: (_options, handlers) => callbacks.push(handlers),
    stop: () => undefined,
    listVoices: () => [],
    dispose: () => undefined,
  };
  return { adapter, callbacks };
}

describe("TtsQueue", () => {
  it("ignores a cancelled utterance's late end after a replacement starts", () => {
    const fake = synthesis();
    const queue = new TtsQueue(fake.adapter);
    let ends = 0;
    queue.onEnd(() => (ends += 1));

    queue.speak({ ...options, text: "A" });
    const a = fake.callbacks[0];
    queue.stop();
    queue.speak({ ...options, text: "B" });
    const b = fake.callbacks[1];

    a?.onEnd();
    expect(ends).toBe(0);

    b?.onEnd();
    expect(ends).toBe(1);
  });
  it("completes the queue when an utterance errors instead of ending", () => {
    const fake = synthesis();
    const queue = new TtsQueue(fake.adapter);
    let ends = 0;
    queue.onEnd(() => (ends += 1));

    queue.speak({ ...options, text: "A" });
    fake.callbacks[0]?.onError();

    // The failed utterance is terminal, so the session stops speaking …
    expect(ends).toBe(1);

    // … and the queue is free to play the next answer.
    queue.speak({ ...options, text: "B" });
    expect(fake.callbacks).toHaveLength(2);
    fake.callbacks[1]?.onEnd();
    expect(ends).toBe(2);
  });
  it("ignores a cancelled utterance's late error", () => {
    const fake = synthesis();
    const queue = new TtsQueue(fake.adapter);
    let ends = 0;
    queue.onEnd(() => (ends += 1));

    queue.speak({ ...options, text: "A" });
    const a = fake.callbacks[0];
    queue.stop();
    queue.speak({ ...options, text: "B" });

    a?.onError();

    expect(ends).toBe(0);
    expect(fake.callbacks).toHaveLength(2);
  });
});
