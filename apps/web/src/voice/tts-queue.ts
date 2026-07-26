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
  #cancelled = false;
  #starts = createEmitter<void>();
  #ends = createEmitter<void>();
  constructor(private readonly adapter: SpeechSynthesisAdapter) {
    adapter.onEnd(() => this.next());
    adapter.onStart(() => this.#starts.emit());
  }
  speak(options: SpeakOptions): void {
    // Some browsers do not dispatch an `end` event after cancel(). A fresh
    // answer must therefore be able to restart the queue immediately.
    this.#cancelled = false;
    this.#items.push({ ...options, text: spokenText(options.text) });
    if (this.#items.length > MAX_TTS_QUEUE)
      this.#items.splice(0, this.#items.length - MAX_TTS_QUEUE);
    if (!this.#playing) this.next();
  }
  stop(): void {
    this.#items = [];
    this.#playing = false;
    this.#cancelled = true;
    this.adapter.stop();
  }
  onStart(listener: () => void): () => void {
    return this.#starts.add(listener);
  }
  onEnd(listener: () => void): () => void {
    return this.#ends.add(listener);
  }
  private next(): void {
    if (this.#cancelled) {
      this.#cancelled = false;
      return;
    }
    const item = this.#items.shift();
    if (!item) {
      this.#playing = false;
      this.#ends.emit();
      return;
    }
    this.#playing = true;
    this.adapter.speak(item);
  }
}
