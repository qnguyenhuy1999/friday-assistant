import { MAX_SPOKEN_CHARS, MAX_TTS_QUEUE } from "./constants";
import { createEmitter } from "./speech-recognition";
import type { SpeakOptions, SpeechSynthesisAdapter } from "./types";
export function spokenText(text: string): string {
  const compact = text.split(/\s+/).filter(Boolean).join(" ");
  return compact.length <= MAX_SPOKEN_CHARS
    ? compact
    : `${compact.slice(0, MAX_SPOKEN_CHARS - 1)}…`;
}
export class TtsQueue {
  #items: SpeakOptions[] = [];
  #playing = false;
  #generation = 0;
  #activeGeneration: number | null = null;
  #starts = createEmitter<void>();
  #ends = createEmitter<void>();
  constructor(private readonly adapter: SpeechSynthesisAdapter) {}
  speak(options: SpeakOptions): void {
    // Some browsers do not dispatch an `end` event after cancel(). A fresh
    // answer must therefore be able to restart the queue immediately.
    this.#items.push({ ...options, text: spokenText(options.text) });
    if (this.#items.length > MAX_TTS_QUEUE)
      this.#items.splice(0, this.#items.length - MAX_TTS_QUEUE);
    if (!this.#playing) this.next();
  }
  stop(): void {
    this.#items = [];
    this.#playing = false;
    // Invalidate callbacks already queued by the browser before cancelling.
    // A late end for an old utterance must not complete its replacement.
    this.#activeGeneration = null;
    this.#generation += 1;
    this.adapter.stop();
  }
  onStart(listener: () => void): () => void {
    return this.#starts.add(listener);
  }
  onEnd(listener: () => void): () => void {
    return this.#ends.add(listener);
  }
  private next(): void {
    const item = this.#items.shift();
    if (!item) {
      this.#playing = false;
      this.#ends.emit();
      return;
    }
    this.#playing = true;
    const generation = ++this.#generation;
    this.#activeGeneration = generation;
    // An utterance is over on either `end` or `error`, and both are terminal
    // for the same generation. Treating only `end` as terminal strands the
    // queue on a failed utterance and the session never stops speaking.
    const finished = () => {
      if (this.#activeGeneration !== generation) return;
      this.#activeGeneration = null;
      this.next();
    };
    this.adapter.speak(item, {
      onStart: () => {
        if (this.#activeGeneration === generation) this.#starts.emit();
      },
      onEnd: finished,
      onError: finished,
    });
  }
}
