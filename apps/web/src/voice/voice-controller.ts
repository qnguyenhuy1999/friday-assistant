import { SILENCE_TIMEOUT_MS } from "./constants";
import { isBenignVoiceError, isPermissionVoiceError } from "./voice-errors";
import type {
  SpeechRecognitionAdapter,
  OutputSpeechController,
  VoiceErrorCode,
  VoiceSnapshot,
} from "./types";
export interface SubmitVoiceInput {
  text: string;
  inputMode: "typed" | "push_to_talk" | "hands_free";
  language: string;
}
export interface TimerApi {
  set(callback: () => void, ms: number): number;
  clear(id: number): void;
}
export interface VoiceControllerDeps {
  recognition: SpeechRecognitionAdapter | null;
  output?: OutputSpeechController | null;
  language: () => string;
  submit(input: SubmitVoiceInput): Promise<void>;
  timers: TimerApi;
  audioLevel?: {
    start(): Promise<void>;
    stop(): void;
    onSustainedSpeech(listener: () => void): () => void;
  } | null;
}
export class VoiceController {
  #snapshot: VoiceSnapshot = {
    state: "idle",
    interimTranscript: "",
    handsFree: false,
    error: null,
  };
  #listeners = new Set<(value: VoiceSnapshot) => void>();
  #session = 0;
  #active: number | null = null;
  #pending: { session: number; mode: "push_to_talk" | "hands_free" } | null =
    null;
  #text = "";
  #permissionRequested = false;
  #silence: number | null = null;
  #disposed = false;
  constructor(private readonly deps: VoiceControllerDeps) {
    deps.recognition?.onResult((result) =>
      this.result(result.transcript, result.isFinal),
    );
    deps.recognition?.onEnd(() => this.ended());
    deps.recognition?.onError((code) => this.error(code));
    deps.audioLevel?.onSustainedSpeech(() => this.bargeIn());
  }
  snapshot(): VoiceSnapshot {
    return this.#snapshot;
  }
  subscribe(listener: (value: VoiceSnapshot) => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }
  patch(value: Partial<VoiceSnapshot>): void {
    this.#snapshot = { ...this.#snapshot, ...value };
    this.#listeners.forEach((listener) => listener(this.#snapshot));
  }
  pressToTalk(): void {
    if (this.#snapshot.handsFree || this.#active !== null) return;
    if (this.#snapshot.state === "speaking") {
      this.stopOutputAndRecognition();
    }
    this.begin("push_to_talk");
  }
  releaseToTalk(): void {
    if (this.#active !== null && !this.#snapshot.handsFree)
      this.finalize(this.#active, "push_to_talk");
  }
  setHandsFree(enabled: boolean): void {
    if (enabled === this.#snapshot.handsFree) return;
    this.patch({ handsFree: enabled });
    if (!enabled) {
      this.clearSilence();
      this.deps.recognition?.abort();
      this.idle();
    } else if (this.deps.recognition) this.begin("hands_free");
    else
      this.patch({ handsFree: false, state: "error", error: "not-supported" });
  }
  async submitTyped(text: string): Promise<void> {
    const trimmed = text.trim();
    if (!trimmed) return;
    await this.submit({
      text: trimmed,
      inputMode: "typed",
      language: this.deps.language(),
    });
  }
  notifyProcessing(): void {
    this.patch({ state: "processing" });
  }
  notifyAwaitingApproval(): void {
    this.patch({ state: "awaiting_approval" });
  }
  notifyResultDelivered(): void {
    this.patch({ state: "idle", interimTranscript: "" });
    if (this.#snapshot.handsFree) this.begin("hands_free");
  }
  speakingStarted(): void {
    this.clearSilence();
    this.deps.recognition?.abort();
    this.#active = null;
    this.patch({ state: "speaking", interimTranscript: "" });
    void this.deps.audioLevel?.start().catch(() => undefined);
  }
  speakingEnded(): void {
    this.deps.audioLevel?.stop();
    this.notifyResultDelivered();
  }
  bargeIn(): void {
    if (this.#snapshot.state !== "speaking") return;
    this.stopOutputAndRecognition();
    if (this.#snapshot.handsFree) this.begin("hands_free");
  }
  interrupt(): void {
    this.clearSilence();
    this.stopOutputAndRecognition();
    this.patch({ handsFree: false, state: "idle", interimTranscript: "" });
    this.#active = null;
    this.#pending = null;
    this.deps.audioLevel?.stop();
  }
  dispose(): void {
    this.#disposed = true;
    this.interrupt();
    this.#listeners.clear();
    this.deps.recognition?.dispose();
  }
  private begin(mode: "push_to_talk" | "hands_free"): void {
    if (this.#disposed || !this.deps.recognition) return;
    this.#active = ++this.#session;
    this.#text = "";
    if (!this.#permissionRequested) {
      this.#permissionRequested = true;
      this.patch({ state: "requesting_permission", error: null });
    }
    this.patch({ state: "listening", interimTranscript: "", error: null });
    this.deps.recognition.start({
      language: this.deps.language(),
      continuous: mode === "hands_free",
    });
  }
  /**
   * Keep browser output and input mutually exclusive.  `cancel()` is
   * synchronous in the Web Speech API, so recognition is only cleared after
   * output has been stopped, before a new recognition session can begin.
   */
  private stopOutputAndRecognition(): void {
    this.deps.output?.stop();
    this.deps.audioLevel?.stop();
    this.deps.recognition?.abort();
    this.#active = null;
    this.#pending = null;
    this.patch({ state: "idle", interimTranscript: "" });
  }
  private result(transcript: string, final: boolean): void {
    if (this.#active === null || this.#disposed) return;
    if (final) {
      this.#text = `${this.#text} ${transcript}`.trim();
      if (this.#snapshot.handsFree) this.armSilence();
    }
    this.patch({
      interimTranscript: final
        ? this.#text
        : `${this.#text} ${transcript}`.trim(),
    });
  }
  private ended(): void {
    const pending = this.#pending;
    if (!pending) return;
    this.#pending = null;
    void this.submit({
      text: this.#text,
      inputMode: pending.mode,
      language: this.deps.language(),
    });
  }
  private finalize(session: number, mode: "push_to_talk" | "hands_free"): void {
    if (session !== this.#active) return;
    this.clearSilence();
    this.#pending = { session, mode };
    this.#active = null;
    this.patch({ state: "finalizing" });
    this.deps.recognition?.stop();
  }
  private async submit(input: SubmitVoiceInput): Promise<void> {
    if (!input.text.trim()) {
      this.idle();
      return;
    }
    this.patch({ state: "submitting", interimTranscript: "", error: null });
    try {
      await this.deps.submit(input);
      this.patch({ state: "processing" });
    } catch {
      this.patch({ state: "error", error: "network" });
    }
  }
  private error(code: VoiceErrorCode): void {
    this.clearSilence();
    if (isBenignVoiceError(code)) {
      this.idle();
      return;
    }
    if (isPermissionVoiceError(code)) this.patch({ handsFree: false });
    this.#active = null;
    this.#pending = null;
    this.patch({ state: "error", error: code });
  }
  private armSilence(): void {
    this.clearSilence();
    const session = this.#active;
    if (session !== null)
      this.#silence = this.deps.timers.set(
        () => this.finalize(session, "hands_free"),
        SILENCE_TIMEOUT_MS,
      );
  }
  private clearSilence(): void {
    if (this.#silence !== null) this.deps.timers.clear(this.#silence);
    this.#silence = null;
  }
  private idle(): void {
    this.#active = null;
    this.#pending = null;
    this.patch({ state: "idle", interimTranscript: "" });
  }
}
