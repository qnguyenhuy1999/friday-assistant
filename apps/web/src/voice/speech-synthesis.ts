import { MAX_TTS_RATE, MIN_TTS_RATE } from "./constants";
import type { SpeechSynthesisAdapter, SpeechSynthesisVoiceInfo } from "./types";
export function createSpeechSynthesisAdapter(
  win: Window,
): SpeechSynthesisAdapter | null {
  const w = win as unknown as {
    speechSynthesis?: {
      getVoices(): SpeechSynthesisVoiceInfo[];
      speak(value: unknown): void;
      cancel(): void;
    };
    SpeechSynthesisUtterance?: new (text: string) => {
      voice: unknown;
      rate: number;
      lang: string;
      onstart: (() => void) | null;
      onend: (() => void) | null;
      onerror: (() => void) | null;
    };
  };
  if (!w.speechSynthesis || !w.SpeechSynthesisUtterance) return null;
  let disposed = false;
  return {
    speak(options, callbacks) {
      if (disposed) return;
      const utterance = new w.SpeechSynthesisUtterance!(options.text);
      utterance.rate = Math.min(
        MAX_TTS_RATE,
        Math.max(MIN_TTS_RATE, options.rate),
      );
      utterance.lang = options.lang;
      utterance.voice =
        w
          .speechSynthesis!.getVoices()
          .find((voice) => voice.voiceURI === options.voiceURI) ?? null;
      utterance.onstart = () => {
        if (!disposed) callbacks.onStart();
      };
      utterance.onend = () => {
        if (!disposed) callbacks.onEnd();
      };
      // A failed utterance fires `error` instead of `end`, so without this the
      // queue would wait forever on an utterance that is already over.
      utterance.onerror = () => {
        if (!disposed) callbacks.onError();
      };
      w.speechSynthesis!.speak(utterance);
    },
    stop() {
      w.speechSynthesis!.cancel();
    },
    listVoices() {
      return w
        .speechSynthesis!.getVoices()
        .map(({ voiceURI, name, lang }) => ({ voiceURI, name, lang }));
    },
    dispose() {
      disposed = true;
      w.speechSynthesis!.cancel();
    },
  };
}
