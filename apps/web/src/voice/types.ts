export type VoiceState =
  | "idle"
  | "requesting_permission"
  | "listening"
  | "finalizing"
  | "submitting"
  | "processing"
  | "awaiting_approval"
  | "speaking"
  | "error";
export type VoiceErrorCode =
  | "not-allowed"
  | "service-not-allowed"
  | "audio-capture"
  | "no-speech"
  | "aborted"
  | "network"
  | "not-supported"
  | "unknown";
export const BENIGN_VOICE_ERRORS: readonly VoiceErrorCode[] = [
  "no-speech",
  "aborted",
];
export interface VoiceCapabilities {
  recognitionSupported: boolean;
  synthesisSupported: boolean;
  microphoneSupported: boolean;
}
export interface VoiceSnapshot {
  state: VoiceState;
  interimTranscript: string;
  handsFree: boolean;
  error: VoiceErrorCode | null;
}
export interface RecognitionResult {
  transcript: string;
  isFinal: boolean;
}
export interface RecognitionStartOptions {
  language: string;
  continuous: boolean;
}
export interface SpeechRecognitionAdapter {
  start(options: RecognitionStartOptions): void;
  stop(): void;
  abort(): void;
  onResult(listener: (result: RecognitionResult) => void): () => void;
  onEnd(listener: () => void): () => void;
  onError(listener: (code: VoiceErrorCode) => void): () => void;
  dispose(): void;
}
export interface SpeechSynthesisVoiceInfo {
  voiceURI: string;
  name: string;
  lang: string;
}
export interface SpeakOptions {
  text: string;
  voiceURI: string | null;
  rate: number;
  lang: string;
}
export interface SpeechSynthesisCallbacks {
  onStart(): void;
  onEnd(): void;
}
export interface SpeechSynthesisAdapter {
  /** Callbacks belong to this utterance, never to a shared synthesis stream. */
  speak(options: SpeakOptions, callbacks: SpeechSynthesisCallbacks): void;
  stop(): void;
  listVoices(): SpeechSynthesisVoiceInfo[];
  dispose(): void;
}
/** The sole output boundary owned by VoiceController. */
export interface OutputSpeechController {
  stop(): void;
}
export interface AudioLevelMonitor {
  start(): Promise<void>;
  stop(): void;
  onSustainedSpeech(listener: () => void): () => void;
  dispose(): void;
}
