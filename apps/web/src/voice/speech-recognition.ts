import type {
  RecognitionStartOptions,
  SpeechRecognitionAdapter,
  VoiceCapabilities,
  VoiceErrorCode,
} from "./types";
interface BrowserRecognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onresult:
    | ((event: {
        resultIndex: number;
        results: ArrayLike<
          ArrayLike<{ transcript: string }> & { isFinal: boolean }
        >;
      }) => void)
    | null;
  onend: (() => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
type RecognitionConstructor = new () => BrowserRecognition;
const KNOWN: readonly VoiceErrorCode[] = [
  "not-allowed",
  "service-not-allowed",
  "audio-capture",
  "no-speech",
  "aborted",
  "network",
];
function ctor(win: Window): RecognitionConstructor | null {
  const w = win as unknown as {
    SpeechRecognition?: RecognitionConstructor;
    webkitSpeechRecognition?: RecognitionConstructor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}
export function createEmitter<T>() {
  const listeners = new Set<(value: T) => void>();
  return {
    add(listener: (value: T) => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    emit(value: T) {
      listeners.forEach((listener) => listener(value));
    },
    clear() {
      listeners.clear();
    },
  };
}
export function detectVoiceCapabilities(win: Window): VoiceCapabilities {
  const w = win as unknown as {
    speechSynthesis?: unknown;
    navigator?: { mediaDevices?: { getUserMedia?: unknown } };
  };
  return {
    recognitionSupported: ctor(win) !== null,
    synthesisSupported: w.speechSynthesis != null,
    microphoneSupported:
      typeof w.navigator?.mediaDevices?.getUserMedia === "function",
  };
}
export function createSpeechRecognitionAdapter(
  win: Window,
): SpeechRecognitionAdapter | null {
  const Recognition = ctor(win);
  if (!Recognition) return null;
  const results = createEmitter<{
    attempt: number;
    transcript: string;
    isFinal: boolean;
  }>();
  const ends = createEmitter<number>();
  const errors = createEmitter<{ attempt: number; code: VoiceErrorCode }>();
  let active: BrowserRecognition | null = null;
  let disposed = false;
  return {
    start(options: RecognitionStartOptions) {
      if (!disposed) {
        const recognition = new Recognition();
        active = recognition;
        recognition.lang = options.language;
        recognition.continuous = options.continuous;
        recognition.interimResults = true;
        recognition.maxAlternatives = 1;
        recognition.onresult = (event) => {
          if (!disposed)
            for (let i = event.resultIndex; i < event.results.length; i += 1) {
              const result = event.results[i];
              if (!result) continue;
              results.emit({
                attempt: options.attempt,
                transcript: result[0]?.transcript ?? "",
                isFinal: Boolean(result.isFinal),
              });
            }
        };
        recognition.onend = () => {
          if (active === recognition) active = null;
          if (!disposed) ends.emit(options.attempt);
        };
        recognition.onerror = (event) => {
          if (!disposed)
            errors.emit({
              attempt: options.attempt,
              code: KNOWN.includes(event.error as VoiceErrorCode)
                ? (event.error as VoiceErrorCode)
                : "unknown",
            });
        };
        recognition.start();
      }
    },
    stop() {
      if (!disposed) active?.stop();
    },
    abort() {
      if (!disposed) active?.abort();
    },
    onResult: results.add,
    onEnd: (listener) => ends.add(listener),
    onError: (listener) =>
      errors.add((event) => listener(event.attempt, event.code)),
    dispose() {
      disposed = true;
      active?.abort();
      active = null;
      results.clear();
      ends.clear();
      errors.clear();
    },
  };
}
