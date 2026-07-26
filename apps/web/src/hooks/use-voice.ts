import { useEffect, useMemo, useRef, useState } from "react";
import {
  createSpeechRecognitionAdapter,
  detectVoiceCapabilities,
} from "../voice/speech-recognition";
import { createSpeechSynthesisAdapter } from "../voice/speech-synthesis";
import { createAudioLevelMonitor } from "../voice/audio-level";
import { VoiceController } from "../voice/voice-controller";
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
  submitRef.current = submit;
  languageRef.current = preferences.language;
  const synthesis = useMemo(() => createSpeechSynthesisAdapter(window), []);
  const audioLevel = useMemo(() => createAudioLevelMonitor(window), []);
  const controller = useMemo(
    () =>
      new VoiceController({
        recognition: createSpeechRecognitionAdapter(window),
        language: () => languageRef.current,
        submit: (input) => submitRef.current(input),
        timers: {
          set: (callback, ms) => window.setTimeout(callback, ms),
          clear: (id) => window.clearTimeout(id),
        },
        audioLevel,
      }),
    [audioLevel],
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
    const start = synthesis.onStart(() => controller.speakingStarted());
    const end = synthesis.onEnd(() => controller.speakingEnded());
    return () => {
      start();
      end();
      synthesis.dispose();
      audioLevel?.dispose();
    };
  }, [synthesis, controller, audioLevel]);
  return {
    controller,
    snapshot,
    capabilities,
    preferences,
    setPreferences(value: VoicePreferences) {
      writeVoicePreferences(value);
      setStored(value);
    },
    synthesis,
  };
}
