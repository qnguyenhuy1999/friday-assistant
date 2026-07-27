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
  attempt: number;
  transcript: string;
  isFinal: boolean;
}
export interface RecognitionStartOptions {
  attempt: number;
  language: string;
  continuous: boolean;
}
export interface SpeechRecognitionAdapter {
  start(options: RecognitionStartOptions): void;
  stop(): void;
  abort(): void;
  onResult(listener: (result: RecognitionResult) => void): () => void;
  onEnd(listener: (attempt: number) => void): () => void;
  onError(
    listener: (attempt: number, code: VoiceErrorCode) => void,
  ): () => void;
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
  /** An utterance that errors never fires `end`, so this is the other way the
   * queue learns it is done with it. Without it a failed utterance strands the
   * queue mid-playback and the session never leaves `speaking`. */
  onError(): void;
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
