import { BENIGN_VOICE_ERRORS, type VoiceErrorCode } from "./types";
export const isBenignVoiceError = (code: VoiceErrorCode) =>
  BENIGN_VOICE_ERRORS.includes(code);
export const isPermissionVoiceError = (code: VoiceErrorCode) =>
  code === "not-allowed" || code === "service-not-allowed";
const messages: Record<VoiceErrorCode, string> = {
  "not-allowed": "Microphone access is blocked. Allow it or type instead.",
  "service-not-allowed": "Speech recognition is unavailable. Type instead.",
  "audio-capture": "No microphone was found. Type instead.",
  "no-speech": "I didn't catch that — try again.",
  aborted: "Listening stopped.",
  network:
    "Speech recognition lost its connection. Try again, or type instead.",
  "not-supported": "Voice is unavailable — type instead.",
  unknown: "Voice input failed. Try again, or type instead.",
};
export const voiceErrorMessage = (code: VoiceErrorCode) => messages[code];
