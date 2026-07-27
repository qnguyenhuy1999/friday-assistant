import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createSpeechRecognitionAdapter,
  detectVoiceCapabilities,
} from "../voice/speech-recognition";
import { createSpeechSynthesisAdapter } from "../voice/speech-synthesis";
import { createAudioLevelMonitor } from "../voice/audio-level";
import { VoiceController } from "../voice/voice-controller";
import { TtsQueue } from "../voice/tts-queue";
import {
  readVoicePreferences,
  writeVoicePreferences,
  type VoicePreferences,
} from "../voice/voice-preferences";
export function useVoice(
  submit: (input: {
    text: string;
    inputMode: "typed" | "push_to_talk" | "hands_free";
    language: string;
  }) => Promise<void>,
) {
  const capabilities = useMemo(() => detectVoiceCapabilities(window), []);
  const [preferences, setStored] = useState<VoicePreferences>(() =>
    readVoicePreferences(),
  );
  const submitRef = useRef(submit);
  const languageRef = useRef(preferences.language);
  const preferencesRef = useRef(preferences);
  submitRef.current = submit;
  languageRef.current = preferences.language;
  preferencesRef.current = preferences;
  const synthesis = useMemo(() => createSpeechSynthesisAdapter(window), []);
  const output = useMemo(
    () => (synthesis ? new TtsQueue(synthesis) : null),
    [synthesis],
  );
  const audioLevel = useMemo(() => createAudioLevelMonitor(window), []);
  const controller = useMemo(
    () =>
      new VoiceController({
        recognition: createSpeechRecognitionAdapter(window),
        output,
        language: () => languageRef.current,
        submit: (input) => submitRef.current(input),
        timers: {
          set: (callback, ms) => window.setTimeout(callback, ms),
          clear: (id) => window.clearTimeout(id),
        },
        audioLevel,
      }),
    [audioLevel, output],
  );
  const [snapshot, setSnapshot] = useState(controller.snapshot());
  useEffect(() => {
    const off = controller.subscribe(setSnapshot);
    return () => {
      off();
      controller.dispose();
    };
  }, [controller]);
  useEffect(() => {
    if (!synthesis) return;
    const start = output?.onStart(() => controller.speakingStarted());
    const end = output?.onEnd(() => controller.speakingEnded());
    return () => {
      start?.();
      end?.();
      synthesis.dispose();
      audioLevel?.dispose();
    };
  }, [synthesis, output, controller, audioLevel]);
  /** Single seam for "an answer arrived". Speaking it is optional; telling the
   * controller the answer was delivered is not — hands-free resumes off that
   * signal, and with speech turned off or unsupported nothing else would
   * report it, leaving the session dead after the first answer.
   *
   * Speaking goes through the queue rather than the adapter, so the controller
   * stays the only thing that can stop output.
   *
   * Identity is stable so a consumer effect keyed on this callback cannot
   * re-fire (and re-speak) on every render. */
  const deliverAnswer = useCallback(
    (summary: string) => {
      const current = preferencesRef.current;
      if (current.enabled && output) {
        output.speak({
          text: summary,
          voiceURI: current.voiceURI,
          rate: current.rate,
          lang: current.language,
        });
        return;
      }
      controller.notifyResultDelivered();
    },
    [controller, output],
  );
  return {
    controller,
    snapshot,
    capabilities,
    deliverAnswer,
    preferences,
    setPreferences(value: VoicePreferences) {
      writeVoicePreferences(value);
      setStored(value);
    },
    output,
  };
}
