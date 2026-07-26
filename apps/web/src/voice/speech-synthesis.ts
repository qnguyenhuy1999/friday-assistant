import { MAX_TTS_RATE, MIN_TTS_RATE } from "./constants";
import { createEmitter } from "./speech-recognition";
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
    };
  };
  if (!w.speechSynthesis || !w.SpeechSynthesisUtterance) return null;
  const starts = createEmitter<void>();
  const ends = createEmitter<void>();
  let disposed = false;
  return {
    speak(options) {
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
        if (!disposed) starts.emit();
      };
      utterance.onend = () => {
        if (!disposed) ends.emit();
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
    onStart: starts.add,
    onEnd: ends.add,
    dispose() {
      disposed = true;
      w.speechSynthesis!.cancel();
      starts.clear();
      ends.clear();
    },
  };
}
