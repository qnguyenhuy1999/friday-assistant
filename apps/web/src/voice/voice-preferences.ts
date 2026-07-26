import {
  DEFAULT_STT_LANGUAGE,
  DEFAULT_TTS_RATE,
  MAX_TTS_RATE,
  MIN_TTS_RATE,
} from "./constants";
export interface VoicePreferences {
  enabled: boolean;
  voiceURI: string | null;
  rate: number;
  language: string;
}
const keys = {
  enabled: "tts.enabled",
  voiceURI: "tts.voiceURI",
  rate: "tts.rate",
  language: "stt.language",
} as const;
export function readVoicePreferences(
  storage: Storage = window.localStorage,
): VoicePreferences {
  const rate = Number(storage.getItem(keys.rate));
  return {
    enabled: storage.getItem(keys.enabled) !== "false",
    voiceURI: storage.getItem(keys.voiceURI),
    rate:
      Number.isFinite(rate) && rate >= MIN_TTS_RATE && rate <= MAX_TTS_RATE
        ? rate
        : DEFAULT_TTS_RATE,
    language: storage.getItem(keys.language) || DEFAULT_STT_LANGUAGE,
  };
}
export function writeVoicePreferences(
  value: VoicePreferences,
  storage: Storage = window.localStorage,
): void {
  storage.setItem(keys.enabled, String(value.enabled));
  if (value.voiceURI) storage.setItem(keys.voiceURI, value.voiceURI);
  else storage.removeItem(keys.voiceURI);
  storage.setItem(keys.rate, String(value.rate));
  storage.setItem(keys.language, value.language);
}
