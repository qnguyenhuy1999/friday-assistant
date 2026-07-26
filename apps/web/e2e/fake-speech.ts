import type { Page } from "@playwright/test";

export async function installFakeSpeech(
  page: Page,
  denyMicrophone = false,
): Promise<void> {
  await page.addInitScript((deny: boolean) => {
    const spoken: string[] = [];
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
        // The fake must retain this exact mutable instance for later callbacks.
        // eslint-disable-next-line @typescript-eslint/no-this-alias
        recognition = this;
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
      voice: unknown = null;
      rate = 1;
      lang = "";
      constructor(readonly text: string) {}
    }
    const synthesis = {
      getVoices: () => [],
      speak: (utterance: Utterance) => {
        spoken.push(utterance.text);
        setTimeout(() => {
          utterance.onstart?.();
          utterance.onend?.();
        }, 0);
      },
      cancel: () => undefined,
    };
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
      spoken: () => [...spoken],
    };
    for (const [key, value] of Object.entries({
      SpeechRecognition: Recognition,
      SpeechSynthesisUtterance: Utterance,
      speechSynthesis: synthesis,
      __fakeSpeech: driver,
    })) {
      Object.defineProperty(window, key, {
        configurable: true,
        writable: true,
        value,
      });
    }
  }, denyMicrophone);
}
