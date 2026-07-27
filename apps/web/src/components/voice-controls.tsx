import { MAX_TTS_RATE, MIN_TTS_RATE } from "../voice/constants";
import type { VoiceCapabilities, VoiceSnapshot } from "../voice/types";
import type { VoicePreferences } from "../voice/voice-preferences";
export function VoiceControls({
  capabilities,
  snapshot,
  preferences,
  disabled = false,
  onPreferences,
  onPress,
  onRelease,
  onHandsFree,
}: {
  capabilities: VoiceCapabilities;
  snapshot: VoiceSnapshot;
  preferences: VoicePreferences;
  /** Voice input has nowhere to go until the conversation exists, so it is
   * held back exactly like Send — speech accepted before then is lost. */
  disabled?: boolean;
  onPreferences(value: VoicePreferences): void;
  onPress(): void;
  onRelease(): void;
  onHandsFree(value: boolean): void;
}) {
  return (
    <fieldset>
      {capabilities.recognitionSupported ? (
        <>
          <button
            type="button"
            aria-label="Hold to talk"
            disabled={disabled}
            onPointerDown={(event) => {
              if (!event.isPrimary || event.button !== 0) return;
              event.currentTarget.setPointerCapture(event.pointerId);
              onPress();
            }}
            onPointerUp={(event) => {
              if (!event.isPrimary || event.button !== 0) return;
              onRelease();
            }}
            onPointerCancel={onRelease}
            onLostPointerCapture={onRelease}
          >
            Hold to talk
          </button>
          <label>
            <input
              type="checkbox"
              disabled={disabled}
              checked={snapshot.handsFree}
              onChange={(event) => onHandsFree(event.target.checked)}
            />
            Hands-free
          </label>
        </>
      ) : (
        <p>Voice input unavailable — type instead.</p>
      )}
      {capabilities.synthesisSupported && (
        <>
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
                onPreferences({
                  ...preferences,
                  rate: Number(event.target.value),
                })
              }
            />
          </label>
        </>
      )}
    </fieldset>
  );
}
