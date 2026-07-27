import {
  AUDIO_LEVEL_POLL_MS,
  BARGE_IN_BASELINE_SAMPLES,
  BARGE_IN_RMS_MULTIPLIER,
  BARGE_IN_SUSTAIN_MS,
} from "./constants";
import { createEmitter } from "./speech-recognition";
import type { AudioLevelMonitor } from "./types";
export const MICROPHONE_CONSTRAINTS = {
  audio: {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  },
} as const;
type Win = Window & {
  AudioContext?: new () => AudioContext;
  webkitAudioContext?: new () => AudioContext;
};
export function createAudioLevelMonitor(
  win: Win,
  timers = window,
): AudioLevelMonitor | null {
  const Context = win.AudioContext ?? win.webkitAudioContext;
  if (!Context || !win.navigator.mediaDevices?.getUserMedia) return null;
  const speech = createEmitter<void>();
  let generation = 0;
  let stream: MediaStream | null = null;
  let context: AudioContext | null = null;
  let timer: number | null = null;
  let baseline: number[] = [];
  let started: number | null = null;
  let emitted = false;
  const stop = () => {
    generation += 1;
    if (timer !== null) timers.clearInterval(timer);
    timer = null;
    stream?.getTracks().forEach((track) => track.stop());
    stream = null;
    void context?.close();
    context = null;
    baseline = [];
    started = null;
    emitted = false;
  };
  return {
    async start() {
      const gen = ++generation;
      const mediaStream = await win.navigator.mediaDevices.getUserMedia(
        MICROPHONE_CONSTRAINTS,
      );
      if (gen !== generation) {
        mediaStream.getTracks().forEach((track) => track.stop());
        return;
      }
      stream = mediaStream;
      context = new Context();
      const analyser = context.createAnalyser();
      context.createMediaStreamSource(stream).connect(analyser);
      const data = new Uint8Array(analyser.fftSize);
      timer = timers.setInterval(() => {
        analyser.getByteTimeDomainData(data);
        const rms = Math.sqrt(
          data.reduce((sum, value) => sum + ((value - 128) / 128) ** 2, 0) /
            data.length,
        );
        if (baseline.length < BARGE_IN_BASELINE_SAMPLES) {
          baseline.push(rms);
          return;
        }
        const threshold =
          (baseline.reduce((sum, value) => sum + value, 0) / baseline.length) *
          BARGE_IN_RMS_MULTIPLIER;
        if (rms > threshold) {
          started ??= Date.now();
          if (!emitted && Date.now() - started >= BARGE_IN_SUSTAIN_MS) {
            emitted = true;
            speech.emit();
          }
        } else {
          started = null;
          emitted = false;
        }
      }, AUDIO_LEVEL_POLL_MS);
    },
    stop,
    onSustainedSpeech: speech.add,
    dispose() {
      stop();
      speech.clear();
    },
  };
}
