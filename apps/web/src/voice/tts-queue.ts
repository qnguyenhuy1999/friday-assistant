import { MAX_SPOKEN_CHARS, MAX_TTS_QUEUE } from "./constants";
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
  constructor(private readonly adapter: SpeechSynthesisAdapter) {
    adapter.onEnd(() => this.next());
  }
  speak(options: SpeakOptions): void {
    this.#items.push({ ...options, text: spokenText(options.text) });
    if (this.#items.length > MAX_TTS_QUEUE)
      this.#items.splice(0, this.#items.length - MAX_TTS_QUEUE);
    if (!this.#playing) this.next();
  }
  stop(): void {
    this.#items = [];
    this.#playing = false;
    this.adapter.stop();
  }
  private next(): void {
    const item = this.#items.shift();
    if (!item) {
      this.#playing = false;
      return;
    }
    this.#playing = true;
    this.adapter.speak(item);
  }
}
