import type { Page } from "@playwright/test";

/** What a spec can drive through `window.__fakeSpeech`. These fakes stand in
 * for the browser's speech and audio hardware only — the real adapters,
 * queue, controller and page run against them unmodified. */
export interface FakeSpeechDriver {
  /** Emit a recognition result on the live recognition session. */
  result(text: string, isFinal?: boolean): void;
  /** End the live recognition session, as the browser does after `stop()`. */
  end(): void;
  /** Error the live recognition attempt before its terminal end callback. */
  error(code: string): void;
  /** Deliver callbacks from the prior attempt after a newer one has started. */
  staleResult(text: string, isFinal?: boolean): void;
  staleError(code: string): void;
  staleEnd(): void;
  /** Text passed to `speechSynthesis.speak`, oldest first. */
  spoken(): string[];
  /** Times `getUserMedia` was called — the microphone-privacy assertion. */
  microphoneRequests(): number;
  /** Recognition sessions started, for asserting hands-free re-arms. */
  recognitionStarts(): number;
  /** Times `speechSynthesis.cancel()` was called — how a barge-in is observed
   * after the transient "Listening…" state has already elapsed. */
  cancels(): number;
  /** Hold utterances open once started, so a spec can act mid-speech. */
  holdSpeech(): void;
  /** Fail the held utterance, as a browser does when synthesis breaks. */
  failSpeech(): void;
  /** Drive the loudness the barge-in monitor samples. */
  setInputLevel(amplitude: number): void;
  /** Loudness samples the monitor has taken. The monitor calibrates its
   * threshold from the first samples, so a spec must let that baseline fill at
   * the quiet level before raising it — otherwise it calibrates to the shout. */
  inputSamples(): number;
}

declare global {
  interface Window {
    __fakeSpeech: FakeSpeechDriver;
  }
}

export async function installFakeSpeech(
  page: Page,
  denyMicrophone = false,
): Promise<void> {
  await page.addInitScript((deny: boolean) => {
    const spoken: string[] = [];
    let cancels = 0;
    let microphoneRequests = 0;
    let recognitionStarts = 0;
    const recognitions: Array<{
      onresult?: (value: unknown) => void;
      onend?: () => void;
      onerror?: (value: { error: string }) => void;
    }> = [];
    let recognition: {
      onresult?: (value: unknown) => void;
      onend?: () => void;
      onerror?: (value: { error: string }) => void;
    } | null = null;
    class Recognition {
      lang = "";
      continuous = false;
      interimResults = false;
      maxAlternatives = 1;
      onresult?: (value: unknown) => void;
      onend?: () => void;
      onerror?: (value: { error: string }) => void;
      start() {
        recognitionStarts += 1;
        // The fake must retain this exact mutable instance for later callbacks.
        // eslint-disable-next-line @typescript-eslint/no-this-alias
        recognition = this;
        recognitions.push(this);
        if (deny) setTimeout(() => this.onerror?.({ error: "not-allowed" }), 0);
      }
      stop() {
        setTimeout(() => this.onend?.(), 0);
      }
      abort() {
        recognition = null;
      }
    }
    class Utterance {
      onstart: (() => void) | null = null;
      onend: (() => void) | null = null;
      onerror: (() => void) | null = null;
      voice: unknown = null;
      rate = 1;
      lang = "";
      constructor(readonly text: string) {}
    }
    let hold = false;
    let held: Utterance | null = null;
    const synthesis = {
      getVoices: () => [],
      speak: (utterance: Utterance) => {
        spoken.push(utterance.text);
        setTimeout(() => {
          utterance.onstart?.();
          if (hold) held = utterance;
          else utterance.onend?.();
        }, 0);
      },
      cancel: () => {
        cancels += 1;
        held = null;
      },
    };

    // Microphone and Web Audio stand-ins. The barge-in monitor's real sampling
    // loop runs against these, so `setInputLevel` exercises its actual
    // baseline, threshold and sustain logic instead of short-circuiting it.
    let amplitude = 1;
    let inputSamples = 0;
    const track = { stop: () => undefined };
    const mediaDevices = {
      getUserMedia: async () => {
        microphoneRequests += 1;
        if (deny) throw new Error("NotAllowedError");
        return { getTracks: () => [track] };
      },
    };
    class FakeAudioContext {
      createAnalyser() {
        return {
          fftSize: 256,
          getByteTimeDomainData(data: Uint8Array) {
            inputSamples += 1;
            data.fill(128 + amplitude);
          },
        };
      }
      createMediaStreamSource() {
        return { connect: () => undefined };
      }
      close() {
        return Promise.resolve();
      }
    }

    const driver = {
      result(text: string, isFinal = true) {
        recognition?.onresult?.({
          resultIndex: 0,
          results: [
            Object.assign([{ transcript: text }], { isFinal, length: 1 }),
          ],
        });
      },
      end() {
        recognition?.onend?.();
      },
      error(code: string) {
        recognition?.onerror?.({ error: code });
      },
      staleError(code: string) {
        recognitions.at(-2)?.onerror?.({ error: code });
      },
      staleResult(text: string, isFinal = true) {
        recognitions.at(-2)?.onresult?.({
          resultIndex: 0,
          results: [
            Object.assign([{ transcript: text }], { isFinal, length: 1 }),
          ],
        });
      },
      staleEnd() {
        recognitions.at(-2)?.onend?.();
      },
      spoken: () => [...spoken],
      cancels: () => cancels,
      microphoneRequests: () => microphoneRequests,
      recognitionStarts: () => recognitionStarts,
      holdSpeech() {
        hold = true;
      },
      failSpeech() {
        hold = false;
        const utterance = held;
        held = null;
        utterance?.onerror?.();
      },
      setInputLevel(value: number) {
        amplitude = value;
      },
      inputSamples: () => inputSamples,
    };
    for (const [key, value] of Object.entries({
      SpeechRecognition: Recognition,
      SpeechSynthesisUtterance: Utterance,
      speechSynthesis: synthesis,
      AudioContext: FakeAudioContext,
      __fakeSpeech: driver,
    })) {
      Object.defineProperty(window, key, {
        configurable: true,
        writable: true,
        value,
      });
    }
    Object.defineProperty(window.navigator, "mediaDevices", {
      configurable: true,
      writable: true,
      value: mediaDevices,
    });
  }, denyMicrophone);
}
