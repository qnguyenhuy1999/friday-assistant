import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useVoice } from "./use-voice";

/** Minimal stand-in for the Web Speech synthesis API, installed before render
 * because the adapter is built once on the hook's first render. */
function installSpeechSynthesis() {
  const spoken: string[] = [];
  class Utterance {
    voice: unknown = null;
    rate = 1;
    lang = "";
    onstart: (() => void) | null = null;
    onend: (() => void) | null = null;
    constructor(public text: string) {}
  }
  Object.defineProperty(window, "speechSynthesis", {
    configurable: true,
    value: {
      getVoices: () => [],
      speak: (utterance: Utterance) => spoken.push(utterance.text),
      cancel: () => undefined,
    },
  });
  Object.defineProperty(window, "SpeechSynthesisUtterance", {
    configurable: true,
    value: Utterance,
  });
  return spoken;
}

afterEach(() => {
  // Unmount before removing the stub: the synthesis adapter cancels speech as
  // it disposes, which needs the API still to be there.
  cleanup();
  Reflect.deleteProperty(window, "speechSynthesis");
  Reflect.deleteProperty(window, "SpeechSynthesisUtterance");
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("useVoice.deliverAnswer", () => {
  it("reports delivery itself when speech is unavailable", () => {
    const { result } = renderHook(() => useVoice(async () => undefined));
    // jsdom exposes no speech synthesis, which is the same position a user is
    // in with "Speak answers" switched off.
    expect(result.current.output).toBeNull();
    const delivered = vi.spyOn(
      result.current.controller,
      "notifyResultDelivered",
    );

    act(() => result.current.deliverAnswer("all done"));

    // Without this, nothing signals delivery and hands-free never resumes.
    expect(delivered).toHaveBeenCalledOnce();
  });

  it("leaves delivery to speech when it will speak the answer", () => {
    const spoken = installSpeechSynthesis();
    const { result } = renderHook(() => useVoice(async () => undefined));
    expect(result.current.output).not.toBeNull();
    const delivered = vi.spyOn(
      result.current.controller,
      "notifyResultDelivered",
    );

    act(() => result.current.deliverAnswer("all done"));

    // Speech end drives the resume here; signalling it now as well would
    // restart listening while Friday is still talking.
    expect(spoken).toEqual(["all done"]);
    expect(delivered).not.toHaveBeenCalled();
  });

  it("reports delivery when the user turned speaking off", () => {
    installSpeechSynthesis();
    const { result } = renderHook(() => useVoice(async () => undefined));
    act(() =>
      result.current.setPreferences({
        ...result.current.preferences,
        enabled: false,
      }),
    );
    const delivered = vi.spyOn(
      result.current.controller,
      "notifyResultDelivered",
    );

    act(() => result.current.deliverAnswer("all done"));

    expect(delivered).toHaveBeenCalledOnce();
  });

  it("keeps a stable identity so a consumer effect cannot re-speak", () => {
    const { result, rerender } = renderHook(() =>
      useVoice(async () => undefined),
    );
    const first = result.current.deliverAnswer;

    rerender();

    expect(result.current.deliverAnswer).toBe(first);
  });
});
