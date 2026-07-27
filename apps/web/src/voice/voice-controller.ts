import { MAX_HANDS_FREE_REARMS, SILENCE_TIMEOUT_MS } from "./constants";
import { isBenignVoiceError, isPermissionVoiceError } from "./voice-errors";
import type {
  SpeechRecognitionAdapter,
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
  #rearms = 0;
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
    this.#rearms = 0;
    this.begin("push_to_talk");
  }
  releaseToTalk(): void {
    if (this.#active !== null && !this.#snapshot.handsFree)
      this.finalize(this.#active, "push_to_talk");
  }
  setHandsFree(enabled: boolean): void {
    if (enabled === this.#snapshot.handsFree) return;
    this.patch({ handsFree: enabled });
    this.#rearms = 0;
    if (!enabled) {
      // Drop the session before aborting so the adapter's end/error callback
      // reads as an end we asked for rather than a recognizer that died.
      this.clearSilence();
      this.#active = null;
      this.#pending = null;
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
    // A delivered answer proves the round trip works, so the restart budget
    // that guards a dying recognizer starts over.
    this.#rearms = 0;
    if (this.#snapshot.handsFree) this.begin("hands_free");
  }
  speakingStarted(): void {
    this.clearSilence();
    this.#active = null;
    this.deps.recognition?.abort();
    this.patch({ state: "speaking", interimTranscript: "" });
    void this.deps.audioLevel?.start().catch(() => undefined);
  }
  speakingEnded(): void {
    this.deps.audioLevel?.stop();
    this.notifyResultDelivered();
  }
  bargeIn(): void {
    if (this.#snapshot.state !== "speaking") return;
    this.deps.audioLevel?.stop();
    this.patch({ state: "idle" });
    if (this.#snapshot.handsFree) this.begin("hands_free");
  }
  interrupt(): void {
    this.clearSilence();
    this.#active = null;
    this.#pending = null;
    this.deps.recognition?.abort();
    this.patch({ handsFree: false, state: "idle", interimTranscript: "" });
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
  private result(transcript: string, final: boolean): void {
    if (this.#active === null || this.#disposed) return;
    // Speech reaching us proves the recognizer is alive.
    this.#rearms = 0;
    if (final) this.#text = `${this.#text} ${transcript}`.trim();
    // Any speech activity restarts the silence window, interim included.
    // Arming only on final results let the timer fire mid-sentence whenever a
    // user kept talking after one phrase was finalized.
    if (this.#snapshot.handsFree) this.armSilence();
    this.patch({
      interimTranscript: final
        ? this.#text
        : `${this.#text} ${transcript}`.trim(),
    });
  }
  private ended(): void {
    if (this.#disposed) return;
    const pending = this.#pending;
    if (pending) {
      this.#pending = null;
      void this.submit({
        text: this.#text,
        inputMode: pending.mode,
        language: this.deps.language(),
      });
      return;
    }
    // No pending stop means the recognizer ended on its own while we still
    // believed a session was live. Without this the controller reports
    // "listening" forever against a dead recognizer.
    if (this.#active === null) return;
    this.clearSilence();
    this.#active = null;
    const captured = this.#text.trim();
    if (captured) {
      void this.submit({
        text: captured,
        inputMode: this.#snapshot.handsFree ? "hands_free" : "push_to_talk",
        language: this.deps.language(),
      });
      return;
    }
    if (this.#snapshot.handsFree) this.rearm();
    else this.idle();
  }
  private finalize(session: number, mode: "push_to_talk" | "hands_free"): void {
    if (session !== this.#active) return;
    this.clearSilence();
    if (mode === "hands_free" && !this.#text.trim()) {
      // Silence elapsed with nothing worth submitting (room noise raised
      // interim results only). Keep the open session rather than churning
      // stop/start; the next result re-arms the window.
      return;
    }
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
    // Captured before idle() clears it: an error against a live session ends
    // the recognizer, while one against an already-dropped session is the
    // echo of an abort we asked for (and must not restart recognition).
    const wasListening = this.#active !== null;
    this.clearSilence();
    if (isBenignVoiceError(code)) {
      this.idle();
      if (wasListening && this.#snapshot.handsFree) this.rearm();
      return;
    }
    if (isPermissionVoiceError(code)) this.patch({ handsFree: false });
    this.#active = null;
    this.#pending = null;
    this.patch({ state: "error", error: code });
  }
  /** Restart hands-free listening after the recognizer ended without being
   * asked to, giving up once the restart budget is spent. */
  private rearm(): void {
    if (this.#disposed || !this.#snapshot.handsFree) return;
    if (this.#rearms >= MAX_HANDS_FREE_REARMS) {
      this.patch({ handsFree: false, state: "error", error: "unknown" });
      return;
    }
    this.#rearms += 1;
    this.begin("hands_free");
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
