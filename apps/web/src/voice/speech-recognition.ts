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
  const recognition = new Recognition();
  const results = createEmitter<{ transcript: string; isFinal: boolean }>();
  const ends = createEmitter<void>();
  const errors = createEmitter<VoiceErrorCode>();
  let disposed = false;
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;
  recognition.onresult = (event) => {
    if (!disposed)
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (!result) continue;
        results.emit({
          transcript: result[0]?.transcript ?? "",
          isFinal: Boolean(result.isFinal),
        });
      }
  };
  recognition.onend = () => {
    if (!disposed) ends.emit();
  };
  recognition.onerror = (event) => {
    if (!disposed)
      errors.emit(
        KNOWN.includes(event.error as VoiceErrorCode)
          ? (event.error as VoiceErrorCode)
          : "unknown",
      );
  };
  return {
    start(options: RecognitionStartOptions) {
      if (!disposed) {
        recognition.lang = options.language;
        recognition.continuous = options.continuous;
        recognition.start();
      }
    },
    stop() {
      if (!disposed) recognition.stop();
    },
    abort() {
      if (!disposed) recognition.abort();
    },
    onResult: results.add,
    onEnd: (listener) => ends.add(listener),
    onError: errors.add,
    dispose() {
      disposed = true;
      recognition.abort();
      results.clear();
      ends.clear();
      errors.clear();
    },
  };
}
