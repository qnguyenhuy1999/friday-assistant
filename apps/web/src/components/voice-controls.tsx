import { MAX_TTS_RATE, MIN_TTS_RATE } from "../voice/constants";
import type { VoiceCapabilities, VoiceSnapshot } from "../voice/types";
import type { VoicePreferences } from "../voice/voice-preferences";
export function VoiceControls({
  capabilities,
  snapshot,
  preferences,
  onPreferences,
  onPress,
  onRelease,
  onHandsFree,
}: {
  capabilities: VoiceCapabilities;
  snapshot: VoiceSnapshot;
  preferences: VoicePreferences;
  onPreferences(value: VoicePreferences): void;
  onPress(): void;
  onRelease(): void;
  onHandsFree(value: boolean): void;
}) {
  if (!capabilities.recognitionSupported)
    return <p>Voice unavailable — type instead.</p>;
  return (
    <fieldset>
      <button
        type="button"
        aria-label="Hold to talk"
        onMouseDown={onPress}
        onMouseUp={onRelease}
      >
        Hold to talk
      </button>
      <label>
        <input
          type="checkbox"
          checked={snapshot.handsFree}
          onChange={(event) => onHandsFree(event.target.checked)}
        />
        Hands-free
      </label>
      <label>
        <input
          aria-label="Speak answers"
          type="checkbox"
          checked={preferences.enabled}
          onChange={(event) =>
            onPreferences({ ...preferences, enabled: event.target.checked })
          }
        />
        Speak answers
      </label>
      <label>
        Rate
        <input
          type="range"
          min={MIN_TTS_RATE}
          max={MAX_TTS_RATE}
          step={0.05}
          value={preferences.rate}
          onChange={(event) =>
            onPreferences({ ...preferences, rate: Number(event.target.value) })
          }
        />
      </label>
    </fieldset>
  );
}
