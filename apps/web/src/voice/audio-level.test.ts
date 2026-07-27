import { describe, expect, it, vi } from "vitest";
import { createAudioLevelMonitor } from "./audio-level";

describe("createAudioLevelMonitor", () => {
  it("stops a stream that resolves after monitoring has been stopped", async () => {
    let resolveStream: ((value: MediaStream) => void) | undefined;
    const stopTrack = vi.fn();
    const stream = {
      getTracks: () => [{ stop: stopTrack }],
    } as unknown as MediaStream;
    const getUserMedia = vi.fn(
      () => new Promise<MediaStream>((resolve) => (resolveStream = resolve)),
    );
    const setInterval = vi.fn(() => 1);
    const monitor = createAudioLevelMonitor(
      {
        navigator: { mediaDevices: { getUserMedia } },
        AudioContext: class {
          createAnalyser() {
            return { fftSize: 32, getByteTimeDomainData: () => undefined };
          }
          createMediaStreamSource() {
            return { connect: () => undefined };
          }
          close() {
            return Promise.resolve();
          }
        },
      } as unknown as Window,
      {
        setInterval,
        clearInterval: vi.fn(),
      } as unknown as Window & typeof globalThis,
    );

    const start = monitor!.start();
    monitor!.stop();
    resolveStream!(stream);
    await start;

    expect(stopTrack).toHaveBeenCalledOnce();
    expect(setInterval).not.toHaveBeenCalled();
  });
});
